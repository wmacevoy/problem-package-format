import sys
d = sys.stdin.buffer.read().split()
n = int(d[0]); v = list(map(int, d[1:1+n]))
print(sum(v), sum(1 for x in v if x % 2 == 0))
