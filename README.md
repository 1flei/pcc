# pcc

Pure PythoC C bindings: a C header parser and code-generation backend written entirely in [PythoC](https://github.com/1flei/PythoC).

Provides the same `files -> symbols` capability as libclang, but with zero external C dependencies. The entire parser, lexer, and PythoC code generator are compiled to native code via PythoC's `@compile` decorator.

## Architecture

```
C source text -> Lexer (native) -> Parser (native) -> AST -> PythoC backend (native) -> .py bindings
```

All pipeline stages run as compiled native code with linear-type memory safety and zero-copy design.

## Modules

| Module | Purpose |
|--------|---------|
| `c_token` | C token definitions (`@enum`) |
| `lexer` | C lexer (`@compile`) |
| `c_ast` | C AST tagged union types (`@compile`) |
| `c_parser` | Recursive descent C parser (`@compile`) |
| `pythoc_backend` | PythoC binding code generator (`@compile`) |
| `bindgen` | End-to-end bindings generation pipeline |

## Dependencies

- `pythoc`
