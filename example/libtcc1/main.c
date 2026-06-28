/* Stepping-stone driver: exercise tinycc's real runtime intrinsics (compiled
   by pcc from lib/libtcc1.c) and print their results. Only exactly-representable
   inputs are used so the output is deterministic, and only the integer results
   are passed to printf (avoiding float/double vararg promotion concerns). */
#include <stdio.h>
#include "intconv.h"

int main(void) {
    printf("fixsfdi 0 = %lld\n", __fixsfdi(0.0f));
    printf("fixsfdi 1 = %lld\n", __fixsfdi(1.0f));
    printf("fixsfdi 2.5 = %lld\n", __fixsfdi(2.5f));
    printf("fixsfdi -2.5 = %lld\n", __fixsfdi(-2.5f));
    printf("fixsfdi 100 = %lld\n", __fixsfdi(100.0f));
    printf("fixsfdi -100 = %lld\n", __fixsfdi(-100.0f));
    printf("fixsfdi 2p24 = %lld\n", __fixsfdi(16777216.0f));
    printf("fixsfdi -2p24 = %lld\n", __fixsfdi(-16777216.0f));

    printf("fixunssfdi 0 = %llu\n", __fixunssfdi(0.0f));
    printf("fixunssfdi 1 = %llu\n", __fixunssfdi(1.0f));
    printf("fixunssfdi 2.5 = %llu\n", __fixunssfdi(2.5f));
    printf("fixunssfdi 100 = %llu\n", __fixunssfdi(100.0f));
    printf("fixunssfdi 2p24 = %llu\n", __fixunssfdi(16777216.0f));

    printf("fixdfdi 0 = %lld\n", __fixdfdi(0.0));
    printf("fixdfdi 2.5 = %lld\n", __fixdfdi(2.5));
    printf("fixdfdi -2.5 = %lld\n", __fixdfdi(-2.5));
    printf("fixdfdi 1e6 = %lld\n", __fixdfdi(1000000.0));
    printf("fixdfdi -1e6 = %lld\n", __fixdfdi(-1000000.0));
    printf("fixdfdi 1e15 = %lld\n", __fixdfdi(1000000000000000.0));
    printf("fixdfdi -1e15 = %lld\n", __fixdfdi(-1000000000000000.0));

    printf("fixunsdfdi 0 = %llu\n", __fixunsdfdi(0.0));
    printf("fixunsdfdi 2.5 = %llu\n", __fixunsdfdi(2.5));
    printf("fixunsdfdi 1e6 = %llu\n", __fixunsdfdi(1000000.0));
    printf("fixunsdfdi 1e15 = %llu\n", __fixunsdfdi(1000000000000000.0));
    printf("fixunsdfdi 2p60 = %llu\n", __fixunsdfdi(1152921504606846976.0));
    return 0;
}
