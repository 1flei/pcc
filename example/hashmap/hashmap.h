#ifndef HASHMAP_H
#define HASHMAP_H

#include <stddef.h>

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

HashMap *hashmap_create(void);
void hashmap_destroy(HashMap *map);
int hashmap_put(HashMap *map, const char *key, long value);
int hashmap_get(const HashMap *map, const char *key, long *out_value);
size_t hashmap_size(const HashMap *map);

#endif
