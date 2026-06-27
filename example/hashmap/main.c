#include "hashmap.h"

#include <stdio.h>

int main(void) {
    HashMap *map = hashmap_create();
    long v;
    int i;
    char namebuf[32];

    if (map == NULL) {
        printf("failed to create map\n");
        return 1;
    }

    hashmap_put(map, "one", 1);
    hashmap_put(map, "two", 2);
    hashmap_put(map, "three", 3);
    hashmap_put(map, "two", 22); /* overwrite */

    /* force several resizes */
    for (i = 0; i < 50; i++) {
        sprintf(namebuf, "key%d", i);
        hashmap_put(map, namebuf, (long)(i * i));
    }

    if (hashmap_get(map, "two", &v))
        printf("two = %ld\n", v);
    if (hashmap_get(map, "three", &v))
        printf("three = %ld\n", v);
    if (hashmap_get(map, "key7", &v))
        printf("key7 = %ld\n", v);
    if (!hashmap_get(map, "missing", &v))
        printf("missing not found\n");

    printf("size = %lu\n", (unsigned long)hashmap_size(map));

    hashmap_destroy(map);
    return 0;
}
