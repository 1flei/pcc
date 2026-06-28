/* Exercises file-scope global lowering: initialized / zero-init / float /
 * pointer globals, address-of, and shadowing by a parameter and a local. */
#include <stdio.h>

int g_counter = 5;
double g_ratio = 1.5;
int g_total;            /* tentative definition -> zero initialized */
int *g_ptr = 0;

void bump(void) {
    g_counter = g_counter + 1;
}

/* The parameter shadows the global: references here must use the parameter. */
int compute(int g_counter) {
    return g_counter * 2;
}

int sum_into(void) {
    int local = 10;     /* a plain local, never a global */
    g_total = g_total + local;
    return g_total;
}

int main(void) {
    bump();
    bump();
    g_ratio = g_ratio * 2.0;
    g_ptr = &g_counter;
    sum_into();
    sum_into();
    printf("counter=%d\n", g_counter);
    printf("ratio=%.1f\n", g_ratio);
    printf("total=%d\n", g_total);
    printf("via_ptr=%d\n", *g_ptr);
    printf("shadow=%d\n", compute(100));
    return 0;
}
