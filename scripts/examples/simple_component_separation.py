# Example from doc
# Simple component separation

import healpy as hp
import pysm
import matplotlib.pyplot as plt

from fgbuster.observation_helpers import get_instrument, get_sky
from fgbuster.component_model import CMB, Dust, Synchrotron
from fgbuster.separation_recipies import basic_comp_sep
from fgbuster.visualization import corner_norm

# Simulate your frequency maps
nside = 32 #32
sky = pysm.Sky(get_sky(nside, 'c1d0s0'))
instrument = pysm.Instrument(get_instrument('litebird', nside))
freq_maps = instrument.observe(sky, write_outputs=False)[0]
freq_maps = freq_maps[:, 1:]  # Select polarization

# Define what you fit for
components = [CMB(), Dust(150.), Synchrotron(20.)]
#components = [CMB(), Dust(150., 1.54, 20.), Synchrotron(20., 70.)]

# Component separation
result = basic_comp_sep(components, instrument, freq_maps)

#Explore the results
print(result.params)
print(result.x)

corner_norm(result.x, result.Sigma, labels=result.params)

print(result.s.shape)

#hp.mollview(result.s[0,1], title='CMB')
#hp.mollview(result.s[1,1], title='Dust', norm='hist')
#hp.mollview(result.s[2,1], title='Synchrotron', norm='hist')
plt.show()