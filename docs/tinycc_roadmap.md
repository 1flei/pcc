# Toward compiling tinycc with pcc

Status and staged roadmap for compiling [tinycc](https://repo.or.cz/tinycc.git)
(`../tinycc/`, ~26k LOC core) with `pcc`. tinycc is the north-star target;
full tcc is a multi-milestone effort. This document records what works today,
the triage-measured remaining gaps, and the ordered plan to a full tcc build.

Reproduce every number below with:

```
cd pcc && PYTHONPATH=. python test/triage_tinycc.py
```

The harness preprocesses each core TU (`-DONE_SOURCE=0`) and runs pcc's
origin-filtered implementation emitter, counting the constructs the backend
still rejects. It is a dashboard, never a pass/fail gate, and never edits
tinycc sources.

## Pipeline (what runs natively)

```
C text -> lexer -> parser -> AST -> PythoC backend -> .py module(s) -> PythoC -> native
```

Single file: `driver.compile_file`. Multiple files linked together:
`driver.compile_project` (one implementation module per `.c`, one interface
module per project `.h`, cross-references satisfied by computed imports, linked
via PythoC `compile_to_executable`).

## Current status (verified)

All 9 host-portable core TUs reach the `emitted` stage (none fail at
preprocess or emission). Global-variable lowering removed the entire
`global-comment` bucket (3238 -> 0); aggregate-initializer and non-structural
goto lowering this iteration took the sentinel count 106 -> 33 and cleared
five TUs (tccgen, tccpp, x86_64-gen, tccasm, tcc) to zero. Remaining backend
rejections, by TU:

| TU            | stage   | unsupported | globals | body lines |
|---------------|---------|-------------|---------|------------|
| tccgen.c      | emitted | 0           | 0       | 8274       |
| tccpp.c       | emitted | 0           | 0       | 3631       |
| x86_64-gen.c  | emitted | 0           | 0       | 2869       |
| tccasm.c      | emitted | 0           | 0       | 1569       |
| tcc.c         | emitted | 0           | 0       | 395        |
| tccdbg.c      | emitted | 2           | 0       | 3807       |
| tccrun.c      | emitted | 4           | 0       | 1997       |
| tccelf.c      | emitted | 9           | 0       | 5205       |
| libtcc.c      | emitted | 18          | 0       | 2706       |
| **total**     |         | **33**      | **0**   |            |

`unsupported` counts `__pcc_unsupported__` sentinels (loud, never silent).
The aggregate-initializer (44) and irreducible-goto (39) buckets are now zero;
all 33 remaining sentinels are unhandled *expression* kinds:

| Category                                | Count | Where                                   |
|-----------------------------------------|-------|-----------------------------------------|
| Variadic error/warning helper calls     | 23    | tcc_error_noabort/tcc_warning(_c) calls |
| Comma-operator / side-effecting cond    | 10    | `x = atoi(o), x != 32` style conditions |

## Done this iteration

- **Triage harness** (`test/triage_tinycc.py`): the dashboard above.
- **PythoC incremental-cache fix**: cross-module `@compile` type layout changes
  now invalidate dependents (`source_embed` dep + `DEPS_VERSION` bump), closing a
  silent-miscompilation gap that surfaced while wiring globals.
- **Global variables** (`emit_var_decl`): each C global lowers to a
  `static[T]` + accessor function; references rewrite to `_pcc_g_<name>()[0]`.
  Handles extern (import accessor from defining module), file-local static, and
  scalar constant initializers. The global-comment bucket is gone (3238 -> 0).
- **goto / label (hybrid)**: laminar C labels/gotos are reconstructed onto
  PythoC's scoped `label` / `goto_end` (forward) / `goto` (backward) primitives,
  expanding scopes in the jump direction. Non-structural cases that no scoped
  label can represent (both-direction labels, jumps between switch cases,
  backward jumps across a loop with an inner switch -- libtcc.c's
  `tcc_parse_args`) now lower the whole function to a `__pcc_pc` state machine:
  every label and synthesized control join is a numbered state in a
  `while True:` dispatch, every goto/branch/loop edge is `__pcc_pc = N;
  continue`, and goto-free constructs stay structural for readability.
  `emit_func_def` routes bodies through `emit_func_body`, which keeps the clean
  structured output when it suffices and otherwise prefers the state machine
  (validated end-to-end vs `cc` in `test_goto_statemachine_pipeline.py`).
- **Aggregate initializers**: uninitialized array/struct/union globals (and
  locals) lower to a seedless `static[T]` slot, zero-initialized per C static-
  duration semantics; positional constant initializers lower to constant
  aggregate seeds. Designated initializers (`.field=`, `[i]=`) are rejected
  loudly rather than miscompiled. Required two small PythoC fixes: seedless
  statics now zero-init (were `undef`), and `try_const_aggregate` builds
  `ir.Constant` aggregates for constant array/struct literals.
- **Stepping-stone**: tinycc's real runtime `lib/libtcc1.c` compiles + links +
  runs through `compile_project`, matching `cc` (`test_libtcc1_stepping_stone.py`).
  Fixed `register` / `auto` storage-class hints (aliased to the no-op `restrict`
  qualifier so they are skipped).
- **Multi-file hardening**
  - **#4 transitive-only headers**: a header reached only indirectly (through an
    umbrella header) now has its interface emitted from a `.i` whose transitive
    include closure actually contains it, and its symbols are imported from the
    full transitive closure (`test_transitive_header.py`).
  - **#7 forward-proto double-emit**: a `.c` that both prototypes and defines a
    function no longer emits an `@extern` plus a `@compile def` for one name (the
    dangerous prototype-after-definition case would otherwise shadow the real
    body). The driver passes each unit's locally-defined names to the emitter,
    which suppresses the redundant prototype in a single parse pass
    (`test_forward_proto.py`).

## Staged roadmap to full tcc

Ordered by triage-measured impact. Each item lists what unblocks and the most
likely implementation seam.

1. **Unhandled expression kinds (33 sites, 100% of the remainder)** - two
   sub-buckets: (a) **variadic calls** to tinycc's `tcc_error_noabort` /
   `tcc_warning` / `tcc_warning_c(warn)(...)` error helpers (23 sites), which
   need variadic-call emission (and, for `tcc_warning_c`, call-returning-
   callable support); and (b) the **comma operator** in side-effecting
   conditions like `if (x = atoi(optarg), x != 32 && x != 64)` (10 sites),
   lowerable via sequenced temporaries hoisted before the condition. GNU
   statement-expressions (`({ ... })`) belong here too where they appear.
   Clearing this bucket takes all 9 host-portable core TUs to zero sentinels.

2. **Bitfields** - the AST already carries `FieldInfo.bit_width` (`c_ast.py`).
   Emit struct fields using PythoC's bitfield layout. Needed before any TU that
   uses bitfielded structs can compile end-to-end (does not show as a sentinel:
   it currently produces wrong layout, so it must land before link-all).

3. **`va_arg` emission** - PythoC exports `va_start` / `va_arg` / `va_end`; emit
   them for variadic function bodies (tinycc's error/printf-style helpers). This
   pairs with item 1's variadic-call emission.

4. **`static inline` emission** - emit inline definitions (currently storage
   class `STORAGE_INLINE` is parsed; the body must be emitted as a normal
   `@compile def`).

5. **static linkage / cross-TU name collisions (#6)** - tinycc reuses
   file-local `static` helper names across many TUs. pcc currently emits every
   defined function as an external-linkage `@compile def` and registers it in
   `func_def_module` by bare name, so linking all TUs together would (a) collide
   on duplicate external symbols and (b) let cross-module import resolution bind
   to the wrong module. The correct fix is to represent internal linkage for
   `@compile def` in PythoC - mirroring the `linkage = 'internal'` already used
   for `static[T]` globals (`PythoC/.../ast_visitor/assignments.py`) - and to
   exclude static functions from `func_def_module`. Deferred because it only
   bites at link-all: every current deliverable (stepping-stone, hashmap,
   transitive, fwdproto) links cleanly with no colliding static names.

6. **link-all** - once 1-5 land, link all core TUs into the `tcc` executable
   via `compile_project` and validate against the stock tcc build.

## Known infrastructure issue (out of scope here)

PythoC's parallel cold build (first run after a `DEPS_VERSION` bump or a wide
source change) can race while several workers rebuild the same native module
concurrently, occasionally segfaulting a worker (leaving `core.*` dumps) and
failing one suite run. A warm re-run is green. This is a PythoC build-system
robustness gap, not a pcc/tinycc issue; serializing the first compile of shared
native modules (or a build-cache lock) would remove it.
