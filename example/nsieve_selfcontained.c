/* Self-contained nsieve: libc functions declared explicitly so the pipeline
 * can be exercised without expanding system headers. */

typedef unsigned long size_t;

void *malloc(size_t size);
void free(void *ptr);
void *memset(void *s, int c, size_t n);
int printf(const char *format, ...);
int atoi(const char *nptr);

typedef unsigned char boolean;

static void nsieve(int m) {
    unsigned int count = 0, i, j;
    boolean *flags = (boolean *)malloc(m * sizeof(boolean));
    memset(flags, 1, m);

    for (i = 2; i < m; ++i)
        if (flags[i]) {
            ++count;
            for (j = i << 1; j < m; j += i)
                flags[j] = 0;
        }

    free(flags);
    printf("Primes up to %8u %8u\n", m, count);
}

int main(int argc, char **argv) {
    int m = atoi(argv[1]);
    for (int i = 0; i < 3; i++)
        nsieve(10000 << (m - i));
    return 0;
}
