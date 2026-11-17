// Accumulates in a 32-bit int: fine for small n, wrong once the sum wraps.
#include <cstdio>
int main(){long long n;if(scanf("%lld",&n)!=1)return 1;int sum=0;long long e=0,v;
for(long long i=0;i<n;++i){scanf("%lld",&v);sum+=(int)v;e+=(v%2==0);}
printf("%lld %lld\n",(long long)sum,e);return 0;}
