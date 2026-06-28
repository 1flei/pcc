#include <stdio.h>

#include "umbrella.h"

int main(void) {
    Pt q;
    q.x = 10;
    q.y = 20;
    printf("helper = %d\n", helper());
    printf("pt_sum = %d\n", pt_sum(&q));
    return 0;
}
