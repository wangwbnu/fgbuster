# Example from doc
# Simple component separation

import healpy as hp
import pysm
import matplotlib
import matplotlib.ticker as ticker
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.gridspec as gridspec
from matplotlib.colors import BoundaryNorm
from matplotlib.ticker import MaxNLocator

plt.rcParams["font.family"] = "serif"
plt.rcParams["font.weight"] = "light"
plt.rc('text', usetex=True)

n=50

#x1, y1, result1 = np.genfromtxt("scripts/examples/data/S_100_145_200_zoom.txt")
#x2, y2, result2 = np.genfromtxt("scripts/examples/data/S_mask_zoom.txt")
#x3, y3, result3 = np.genfromtxt("scripts/examples/data/S_mask_lmin30_zoom.txt")
x1, y1, result1 = np.genfromtxt("scripts/examples/data/trueS_full_noise_zoom.txt")
x2, y2, result2 = np.genfromtxt("scripts/examples/data/trueS_mask_noise_zoom.txt")
x3, y3, result3 = np.genfromtxt("scripts/examples/data/trueS_mask_lmin30_noise_zoom.txt")

x1 = x1.reshape((n, n))
y1 = y1.reshape((n, n))
result1 = result1.reshape((n, n))
x2 = x2.reshape((n, n))
y2 = y2.reshape((n, n))
result2 = result2.reshape((n, n))
x3 = x3.reshape((n, n))
y3 = y3.reshape((n, n))
result3 = result3.reshape((n, n))


levels1 = np.array([result1.max()-4.5, result1.max()-2, result1.max()-0.5])
ind1 = np.unravel_index(np.argmax(result1, axis=None), result1.shape)
levels2 = np.array([result2.max()-4.5, result2.max()-2, result2.max()-0.5])
ind2 = np.unravel_index(np.argmax(result2, axis=None), result2.shape)
levels3 = np.array([result3.max()-4.5, result3.max()-2, result3.max()-0.5])
ind3 = np.unravel_index(np.argmax(result3, axis=None), result3.shape)

fig, ax1 = plt.subplots(figsize=(10 ,10))


c1 = ax1.contour(x1, y1, result1, levels=levels1, colors='r', linewidths=2.0)
ax1.plot(x1[ind1], y1[ind1], 'r+')
c2 = ax1.contour(x2, y2, result2, levels=levels2, colors='b', linewidths=2.0)
ax1.plot(x2[ind2], y2[ind2], 'b+')
c3 = ax1.contour(x3, y3, result3, levels=levels3, colors='g', linewidths=2.0)
ax1.plot(x3[ind3], y3[ind3], 'g+')
ax1.plot(0.45839486, 2.34451664, 'k+')
labels=['Full sky', 'Masked sky', r'Masked sky, $\ell_{min} = 30$']
c1.collections[0].set_label(labels[0])
c2.collections[1].set_label(labels[1])
c3.collections[2].set_label(labels[2])
ax1.legend(loc='lower left', prop={'size':18})
ax1.set_xlabel(r'$A_{01}$', fontsize=25, labelpad=15)
ax1.set_ylabel(r'$A_{21}$', fontsize=25, labelpad=10)
#cbar1.ax.set_ylabel(r'$\mathrm{ln} \mathcal{L}$', rotation=270, fontsize=25, labelpad=40)
ax1.tick_params(axis='both', which='major', labelsize=15, length=10, width=1.5)
#cbar1.ax.tick_params(axis='both', which='major', labelsize=15, length=10, width=1.5)
fmt1 = {}
fmt2 = {}
fmt3 = {}
strs = [r'$3 \sigma$', r'$2 \sigma$', r'$1 \sigma$']
for l, s in zip(c1.levels, strs):
    fmt1[l] = s
    fmt2[l] = s
    fmt3[l] = s
#ax1.clabel(c1, c1.levels, inline=True, fmt=fmt1, usetex=True, fontsize=15, weight='Bold')
#ax1.clabel(c2, c2.levels, inline=True, fmt=fmt2, usetex=True)
#ax1.clabel(c3, c3.levels, inline=True, fmt=fmt3, usetex=True)

# adjust spacing between subplots so `ax1` title and `ax0` tick labels
# don't overlap

#fig.tight_layout(pad=3.0)
#fig.savefig('scripts/examples/plots/S_mask_lmin30.png')
fig.savefig('scripts/examples/plots/contour_comp_trueS_noise.png')
#plt.show()

#Explore the results
#print(result.params)
#print(result.x)

#corner_norm(result.x, result.Sigma, labels=result.params)

#print(result.s.shape)

#hp.mollview(result.s[0,1], title='CMB')
#hp.mollview(result.s[1,1], title='Dust', norm='hist')
#hp.mollview(result.s[2,1], title='Synchrotron', norm='hist')
#plt.show()