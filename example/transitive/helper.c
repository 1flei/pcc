#include "umbrella.h"

int pt_sum(Pt *p) {
    return p->x + p->y;
}

int helper(void) {
    Pt p;
    p.x = 3;
    p.y = 4;
    return pt_sum(&p);
}
