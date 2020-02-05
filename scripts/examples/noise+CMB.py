# Example from doc
# Simple component separation

import healpy as hp
import pysm
from pysm.nominal import models
from pysm.common import loadtxt
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.gridspec as gridspec
from matplotlib.colors import BoundaryNorm
from matplotlib.ticker import MaxNLocator
from matplotlib.ticker import NullFormatter
from matplotlib.ticker import MultipleLocator
from fgbuster.observation_helpers import get_instrument, get_sky
from fgbuster.component_model import SemiBlind, CMB, Dust, Synchrotron
#from fgbuster.component_model import CMB, Dust, Synchrotron
from fgbuster.separation_recipies import noise_real_max, test_fisher
from fgbuster.visualization import corner_norm
from fgbuster.mixingmatrix import MixingMatrix
from fgbuster.algebra import _mm, _mtmm


def get_max(nside, instrument, cmb_seed, mask=None, fsky=1.):

    # Simulate frequency maps
    cmb = {
        'model': 'taylens',
        'cmb_specs': loadtxt('/home/clement/Documents/Projets/Physique/Postdoc_APC/Software/CAMB_Nov13/fgbuster/test_lenspotentialCls.dat', mpi_comm=None, unpack=True),
        'delens': False,
        'delensing_ells': loadtxt('/home/clement/Documents/Projets/Physique/Postdoc_APC/Software/PySM_public/pysm/template/delens_ells.txt', mpi_comm=None),
        'nside': nside,
        'cmb_seed': cmb_seed
    }
    dust = models("d0", nside)
    sky_config = {'cmb' : [cmb], 'dust' : dust} # a la PySM
    sky = pysm.Sky(sky_config)
    freq_maps = instrument.observe(sky, write_outputs=False)[0]
    #freq_maps = freq_maps[:, 1:]  # Select polarization
    #print(sky.Components)
    if mask is not None:
      freq_maps *= mask #apply mask to sky maps


    # Define what you fit for
    components = [CMB(), Dust(150.)]
    nblind = len(components)-1

    #Inverse noise matrix
    lmax = 3*nside-1
    bl = [hp.gauss_beam(np.radians(b/60.), lmax=3*nside-1) for b in instrument.Beams]
    inv_Nl = (np.array(bl) / np.radians(instrument.Sens_P/60.)[:, np.newaxis])**2
    inv_Nl = np.array([np.diag(inv_Nl[:,l]) for l in np.arange(inv_Nl.shape[1])])#[2:,np.newaxis,:,:]
    inv_N = np.diag(hp.nside2resol(nside, arcmin=True) / (instrument.Sens_P))**2
    
    #CMB prior covariance matrix
    #templates = '/mnt/PersoPro/Documents/Projets/Physique/Postdoc_APC/Software/fgbuster/fgbuster/templates/Cls_Planck2018_lensed_scalar.fits'
    templates = np.swapaxes(np.genfromtxt("../CAMB_Nov13/fgbuster/test_lenspotentialCls.dat", usecols=(1, 2, 3)), 0, 1)

    np.random.seed(cmb_seed)
    noise_seed = np.random.randint(100000)
    m = noise_real_max(components, instrument, templates, freq_maps, fsky, nside, inv_Nl, noise_seed, nblind)
    
    return m


nside = 32
npts = 1300
cmb_seed = np.arange(npts) + 700
#outfile = "scripts/examples/data/noise_CMB_so_nomask.txt"
outfile = open("scripts/examples/data/noise_CMB_so_nomask.txt", "a")

#mask = hp.read_map("/mnt/PersoPro/Documents/Projets/Physique/Postdoc_APC/Software/fgbuster/fgbuster/templates/HFI_Mask_GalPlane-apo2_2048_R2.00.fits", field=(2))
#mask = hp.ud_grade(mask, nside_out=nside)
#fsky = float(mask.sum()) / mask.size
#instrument = pysm.Instrument(get_instrument('semiblind_test', nside))
instrument = pysm.Instrument(get_instrument('so_la', nside))

x = np.zeros((npts, len(instrument.Frequencies)-1))
for i in np.arange(npts):
    print("step ", i)
    #x[i, :] = get_max(nside, instrument, cmb_seed[i], mask, fsky)
    x[i, :] = get_max(nside, instrument, cmb_seed[i])

#x, y = get_max(nside, cmb_seed, n_noise)
#x = x.flatten()
#y = y.flatten()

np.savetxt(outfile, x)
