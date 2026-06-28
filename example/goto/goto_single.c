/* Exercises C goto/label lowering onto PythoC's scoped label/goto/goto_end:
 * a forward chain to ordered cleanup/return labels, a backward loop label, and
 * a nested cleanup ladder where goto_end falls through outer cleanups. */
#include <stdio.h>

/* Forward gotos to a chain of cleanup/return labels. */
int classify(int x) {
    int result = 0;
    if (x < 0)
        goto negative;
    if (x == 0)
        goto zero;
    result = 1;
    goto done;
negative:
    result = -1;
    goto done;
zero:
    result = 0;
done:
    return result;
}

/* Backward goto forming a loop, plus a forward goto out of it. */
int sum_to(int n) {
    int i = 1;
    int total = 0;
loop:
    if (i > n)
        goto end;
    total = total + i;
    i = i + 1;
    goto loop;
end:
    return total;
}

/* Nested cleanup ladder: goto_end fall-through across outer cleanups. */
int ladder(int a, int b) {
    int acc = 0;
    acc = acc + 1;
    if (a)
        goto fail1;
    acc = acc + 10;
    if (b)
        goto fail2;
    acc = acc + 100;
    return acc;
fail2:
    acc = acc + 1000;
fail1:
    acc = acc + 10000;
    return acc;
}

int main(void) {
    printf("classify(-5)=%d\n", classify(-5));
    printf("classify(0)=%d\n", classify(0));
    printf("classify(9)=%d\n", classify(9));
    printf("sum_to(5)=%d\n", sum_to(5));
    printf("ladder(0,0)=%d\n", ladder(0, 0));
    printf("ladder(1,0)=%d\n", ladder(1, 0));
    printf("ladder(0,1)=%d\n", ladder(0, 1));
    return 0;
}
