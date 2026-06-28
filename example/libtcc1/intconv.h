/* Prototypes for the float/double -> 64-bit integer conversion helpers that
   tinycc's runtime (lib/libtcc1.c) provides on x86_64. Declared here so the
   driver can call them and so pcc's multi-file machinery imports them from the
   libtcc1 implementation module. The long double helpers are intentionally
   omitted: pcc maps `long double` to f64, so they would diverge from cc. */
#ifndef PCC_LIBTCC1_INTCONV_H
#define PCC_LIBTCC1_INTCONV_H

long long __fixsfdi(float a1);
unsigned long long __fixunssfdi(float a1);
long long __fixdfdi(double a1);
unsigned long long __fixunsdfdi(double a1);

#endif
