# Example from doc
# Simple component separation

import healpy as hp
import pysm
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.gridspec as gridspec
from matplotlib.colors import BoundaryNorm
from matplotlib.ticker import MaxNLocator

from fgbuster.observation_helpers import get_instrument, get_sky
from fgbuster.component_model import SemiBlind, CMB, Dust, Synchrotron
#from fgbuster.component_model import CMB, Dust, Synchrotron
from fgbuster.separation_recipies import test_fisher
from fgbuster.visualization import corner_norm
from fgbuster.mixingmatrix import MixingMatrix
from fgbuster.algebra import _mm, _mtmm

# Simulate your frequency maps
nside = 32
lmax = 3*nside-1

# Define sky configuration
sky = pysm.Sky(get_sky(nside, 'c1d0')) # a la fgbuster
instrument = pysm.Instrument(get_instrument('semiblind_test', nside))
freq_maps = instrument.observe(sky, write_outputs=False)[0]


# Define what you fit for
components = [CMB(), Dust(150.)]
nblind = len(components)-1

#Inverse noise matrix
bl = [hp.gauss_beam(np.radians(b/60.), lmax=3*nside-1) for b in instrument.Beams]
inv_Nl = (np.array(bl) / np.radians(instrument.Sens_P/60.)[:, np.newaxis])**2
inv_Nl = np.array([np.diag(inv_Nl[:,l]) for l in np.arange(inv_Nl.shape[1])])
inv_N = np.diag(hp.nside2resol(nside, arcmin=True) / (instrument.Sens_P))**2

    
#CMB prior covariance matrix
#templates = '/mnt/PersoPro/Documents/Projets/Physique/Postdoc_APC/Software/fgbuster/fgbuster/templates/Cls_Planck2018_lensed_scalar.fits'
templates = np.swapaxes(np.genfromtxt("../CAMB_Nov13/fgbuster/test_lenspotentialCls.dat", usecols=(1, 2, 3)), 0, 1)

#L_brute, L_param, L_ana = test_fisher(components, instrument, templates, freq_maps, nside, nblind, inv_Nl)
L_ana = test_fisher(components, instrument, templates, freq_maps, nside, nblind, inv_Nl)

#print("Brute force : ", L_brute)
#print("Analytical parametric expression : ", L_param)
print("Analytical semiblind expression : ", L_ana)
#print("Absolute difference : ", L_ana - L_param)
#print("Relative difference : ", (L_ana - L_param)/L_brute)