/* Umbrella header: re-exports base.h and adds its own declaration. Consumers
   include only this header, so base.h is reached transitively. */
#ifndef PCC_TRANSITIVE_UMBRELLA_H
#define PCC_TRANSITIVE_UMBRELLA_H

#include "base.h"

int helper(void);

#endif
