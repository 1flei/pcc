/* Single-translation-unit amalgamation of the hashmap task, for exercising
   the current single-file pcc pipeline. The multi-file version (hashmap.h /
   hashmap.c / main.c) is the real target for separate compilation. */

#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define HM_INITIAL_CAPACITY 8
#define HM_MAX_LOAD_NUM 3
#define HM_MAX_LOAD_DEN 4

typedef struct HashEntry {
    char *key;
    long value;
    struct HashEntry *next;
} HashEntry;

typedef struct HashMap {
    HashEntry **buckets;
    size_t capacity;
    size_t size;
} HashMap;

static unsigned long hash_string(const char *s) {
    /* djb2 */
    unsigned long h = 5381;
    int c;
    while ((c = (unsigned char)*s++) != 0)
        h = ((h << 5) + h) + (unsigned long)c;
    return h;
}

static char *dup_string(const char *s) {
    size_t n = strlen(s) + 1;
    char *copy = (char *)malloc(n);
    if (copy != NULL)
        memcpy(copy, s, n);
    return copy;
}

HashMap *hashmap_create(void) {
    HashMap *map = (HashMap *)malloc(sizeof(HashMap));
    if (map == NULL)
        return NULL;
    map->capacity = HM_INITIAL_CAPACITY;
    map->size = 0;
    map->buckets = (HashEntry **)calloc(map->capacity, sizeof(HashEntry *));
    if (map->buckets == NULL) {
        free(map);
        return NULL;
    }
    return map;
}

void hashmap_destroy(HashMap *map) {
    size_t i;
    if (map == NULL)
        return;
    for (i = 0; i < map->capacity; i++) {
        HashEntry *e = map->buckets[i];
        while (e != NULL) {
            HashEntry *next = e->next;
            free(e->key);
            free(e);
            e = next;
        }
    }
    free(map->buckets);
    free(map);
}

static int hashmap_resize(HashMap *map, size_t new_capacity) {
    HashEntry **new_buckets;
    size_t i;
    new_buckets = (HashEntry **)calloc(new_capacity, sizeof(HashEntry *));
    if (new_buckets == NULL)
        return 0;
    for (i = 0; i < map->capacity; i++) {
        HashEntry *e = map->buckets[i];
        while (e != NULL) {
            HashEntry *next = e->next;
            size_t idx = (size_t)(hash_string(e->key) % new_capacity);
            e->next = new_buckets[idx];
            new_buckets[idx] = e;
            e = next;
        }
    }
    free(map->buckets);
    map->buckets = new_buckets;
    map->capacity = new_capacity;
    return 1;
}

int hashmap_put(HashMap *map, const char *key, long value) {
    size_t idx;
    HashEntry *e;
    if (map == NULL || key == NULL)
        return 0;

    idx = (size_t)(hash_string(key) % map->capacity);
    for (e = map->buckets[idx]; e != NULL; e = e->next) {
        if (strcmp(e->key, key) == 0) {
            e->value = value;
            return 1;
        }
    }

    if (map->size * HM_MAX_LOAD_DEN >= map->capacity * HM_MAX_LOAD_NUM) {
        if (!hashmap_resize(map, map->capacity * 2))
            return 0;
        idx = (size_t)(hash_string(key) % map->capacity);
    }

    e = (HashEntry *)malloc(sizeof(HashEntry));
    if (e == NULL)
        return 0;
    e->key = dup_string(key);
    if (e->key == NULL) {
        free(e);
        return 0;
    }
    e->value = value;
    e->next = map->buckets[idx];
    map->buckets[idx] = e;
    map->size++;
    return 1;
}

int hashmap_get(const HashMap *map, const char *key, long *out_value) {
    size_t idx;
    HashEntry *e;
    if (map == NULL || key == NULL)
        return 0;
    idx = (size_t)(hash_string(key) % map->capacity);
    for (e = map->buckets[idx]; e != NULL; e = e->next) {
        if (strcmp(e->key, key) == 0) {
            if (out_value != NULL)
                *out_value = e->value;
            return 1;
        }
    }
    return 0;
}

size_t hashmap_size(const HashMap *map) {
    return map != NULL ? map->size : 0;
}

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
