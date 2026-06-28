/* Base header: only ever included indirectly (through umbrella.h). Exercises
   pcc's multi-file handling of a transitively-included interface - its type
   and function declaration must reach every consuming module even though no
   .c includes it directly. */
#ifndef PCC_TRANSITIVE_BASE_H
#define PCC_TRANSITIVE_BASE_H

typedef struct Pt {
    int x;
    int y;
} Pt;

int pt_sum(Pt *p);

#endif
