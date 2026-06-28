/* Exercises non-structural goto lowered to a function-level state machine.
 * These shapes cannot map onto PythoC's scoped label/goto/goto_end (both-
 * direction labels, jumps between switch cases, backward jumps across a loop
 * with an inner switch), so pcc dissolves the whole function into a
 * `__pcc_pc` while-loop dispatch. The cleanup-ladder function stays on the
 * structured path, proving the hybrid selection. */
#include <stdio.h>

/* Both-direction label: forward entry and backward loop onto `mid`. */
int bidir(int x) {
    if (x == 5)
        goto mid;
    x = x + 1;
mid:
    x = x + 1;
    if (x < 10)
        goto mid;
    return x;
}

/* goto from one switch case into a label living inside another case. */
int pick(int k, int x) {
    switch (k) {
        case 1:
            x = 10;
        set_it:
            x = x + 1;
            break;
        case 2:
            x = 20;
            goto set_it;
        default:
            x = 0;
    }
    return x;
}

/* "reparse" idiom: a backward goto across a for loop that contains a switch
 * with a forward goto to a label after the switch. */
int reparse(int n) {
    int total = 0;
    int redo = 0;
reparse:
    for (int i = 0; i < n; i++) {
        switch (i % 3) {
            case 0:
                total += 1;
                break;
            case 1:
                total += 2;
                goto next;
            default:
                total += 3;
        }
        total += 10;
    next:
        total += 0;
    }
    if (redo == 0) {
        redo = 1;
        n = n - 1;
        goto reparse;
    }
    return total;
}

/* Laminar forward cleanup jump out of nested loops: stays structured. */
int findpair(int a, int b) {
    int found = 0;
    for (int i = 0; i < a; i++) {
        for (int j = 0; j < b; j++) {
            if (i * j == 6) {
                found = i * 100 + j;
                goto done;
            }
        }
    }
done:
    return found;
}

int main(void) {
    printf("bidir(3)=%d\n", bidir(3));
    printf("bidir(5)=%d\n", bidir(5));
    printf("bidir(20)=%d\n", bidir(20));
    printf("pick(1,0)=%d\n", pick(1, 0));
    printf("pick(2,0)=%d\n", pick(2, 0));
    printf("pick(9,0)=%d\n", pick(9, 0));
    printf("reparse(4)=%d\n", reparse(4));
    printf("reparse(0)=%d\n", reparse(0));
    printf("findpair(5,5)=%d\n", findpair(5, 5));
    printf("findpair(2,2)=%d\n", findpair(2, 2));
    return 0;
}
