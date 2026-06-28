/* A translation unit that both forward-declares and defines its functions - a
   ubiquitous C pattern, and pervasive in tinycc. Emitting the prototype as an
   @extern in addition to the @compile def would create two bindings for one
   name; when the prototype follows the definition (square, below) the @extern
   would even shadow the real definition. The implementation emitter must drop
   prototypes the unit also defines. */
#include "fwdlib.h"

int add_one(int x);   /* prototype before definition */

int add_one(int x) {
    return x + 1;
}

int square(int x) {
    return x * x;
}

int square(int x);    /* redundant prototype AFTER the definition */
