# FGBuster
# Copyright (C) 2019 Davide Poletti, Josquin Errard and the FGBuster developers
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

""" Forecasting toolbox
"""
import os.path as op
import numpy as np
import pylab as pl
import healpy as hp
import scipy as sp
from .algebra import comp_sep, W_dBdB, W_dB, W, _mmm, _utmv, _mmv, _mv, _T, _mtmm
from .mixingmatrix import MixingMatrix
from .separation_recipes import _format_alms, _format_bls, _r_to_c_alms, _get_modified_A, _get_modified_A_dBdB
from .observation_helpers import standardize_instrument
import sys
import matplotlib.colors as col
from .algebra import fisher_logL_dB_dB

__all__ = [
    'xForecast',
    'harmonic_xForecast',
    'harmonic_xForecast_fsl',
]


CMB_CL_FILE = op.join(
     op.dirname(__file__), 'templates/Cls_Planck2018_%s.fits')


def xForecast(components, instrument, d_fgs, lmin, lmax,
              Alens=1.0, r=0.001, make_figure=False,
              **minimize_kwargs):
    """ xForecast

    Run XForcast (Stompor et al, 2016) using the provided instrumental
    specifications and input foregrounds maps. If the foreground maps match the
    components provided (constant spectral indices are assumed), it reduces to
    CMB4cast (Errard et al, 2011). Currently, only polarization is considered
    fot component separation and only the BB power spectrum for cosmological
    analysis.

    Parameters
    ----------
    components: list
         `Components` of the mixing matrix
    instrument:
        Object that provides the following as a key or an attribute.

        - **frequency**
        - **depth_p** (optional, frequencies are inverse-noise
          weighted according to these noise levels)
        - **fwhm** (optional)

        They can be anything that is convertible to a float numpy array.
    d_fgs: ndarray
        The foreground maps. No CMB. Shape `(n_freq, n_stokes, n_pix)`.
        If some pixels have to be masked, set them to zero.
        Since (cross-)spectra of the maps will be computed, you might want to
        apodize your mask (use the same apodization for all the frequency).
    lmin: int
        minimum multipole entering the likelihood computation
    lmax: int
        maximum multipole entering the likelihood computation
    Alens: float
        Amplitude of the lensing B-modes entering the likelihood on r
    r: float
        tensor-to-scalar ratio assumed in the likelihood on r
    minimize_kwargs: dict
        Keyword arguments to be passed to `scipy.optimize.minimize` during
        the fitting of the spectral parameters.
        A good choice for most cases is
        `minimize_kwargs = {'tol': 1, options: {'disp': True}}`. `tol` depends
        on both the solver and your signal to noise: it should ensure that the
        difference between the best fit -logL and and the minimum is well less
        then 1, without exagereting (a difference of 1e-4 is useless).
        `disp` also triggers a verbose callback that monitors the convergence.

    Returns
    -------
    xFres: dict
        xForecast result. It includes

        - the fitted spectral parameters
        - noise-averaged post-component separation CMB power spectrum

          - noise spectrum
          - statistical residuals spectrum
          - systematic residuals spectrum

        - noise-averaged cosmological likelihood

    """
    # Preliminaries
    instrument = standardize_instrument(instrument)
    nside = hp.npix2nside(d_fgs.shape[-1])
    n_stokes = d_fgs.shape[1]
    n_freqs = d_fgs.shape[0]
    invN = np.diag(hp.nside2resol(nside, arcmin=True) / (instrument.depth_p))**2
    mask = d_fgs[0, 0, :] != 0.
    fsky = mask.astype(float).sum() / mask.size
    ell = np.arange(lmin, lmax+1)
    #print('fsky = ', fsky)

    ############################################################################
    # 1. Component separation using the noise-free foregrounds templare
    # grab the max-L spectra parameters with the associated error bars
    print('======= ESTIMATION OF SPECTRAL PARAMETERS =======')
    A = MixingMatrix(*components)
    A_ev = A.evaluator(instrument.frequency)
    A_dB_ev = A.diff_evaluator(instrument.frequency)

    x0 = np.array([x for c in components for x in c.defaults])
    if n_stokes == 3:  # if T and P were provided, extract P
        d_comp_sep = d_fgs[:, 1:, :]
    else:
        d_comp_sep = d_fgs

    res = comp_sep(A_ev, d_comp_sep.T, invN, A_dB_ev, A.comp_of_dB, x0, **minimize_kwargs)

    res.params = A.params
    res.s = res.s.T
    A_maxL = A_ev(res.x)
    A_dB_maxL = A_dB_ev(res.x)
    A_dBdB_maxL = A.diff_diff_evaluator(instrument.frequency)(res.x)

    print('res.x = ', res.x)

    ############################################################################
    # 2. Estimate noise after component separation
    ### A^T N_ell^-1 A
    print('======= ESTIMATION OF NOISE AFTER COMP SEP =======')
    i_cmb = A.components.index('CMB')
    Cl_noise = _get_Cl_noise(instrument, A_maxL, lmax)[i_cmb, i_cmb, lmin:]

    ############################################################################
    # 3. Compute spectra of the input foregrounds maps
    ### TO DO: which size for Cl_fgs??? N_spec != 1 ? 
    print ('======= COMPUTATION OF CL_FGS =======')
    if n_stokes == 3:  
        d_spectra = d_fgs
    else:  # Only P is provided, add T for map2alm
        d_spectra = np.zeros((n_freqs, 3, d_fgs.shape[2]), dtype=d_fgs.dtype)
        d_spectra[:, 1:] = d_fgs

    # Compute cross-spectra
    almBs = [hp.map2alm(freq_map, lmax=lmax, iter=10)[2] for freq_map in d_spectra]
    Cl_fgs = np.zeros((n_freqs, n_freqs, lmax+1), dtype=d_fgs.dtype)
    for f1 in range(n_freqs):
        for f2 in range(n_freqs):
            if f1 > f2:
                Cl_fgs[f1, f2] = Cl_fgs[f2, f1]
            else:
                Cl_fgs[f1, f2] = hp.alm2cl(almBs[f1], almBs[f2], lmax=lmax)

    Cl_fgs = Cl_fgs[..., lmin:] / fsky

    ############################################################################
    # 4. Estimate the statistical and systematic foregrounds residuals
    print('======= ESTIMATION OF STAT AND SYS RESIDUALS =======')

    W_maxL = W(A_maxL, invN=invN)[i_cmb, :]
    W_dB_maxL = W_dB(A_maxL, A_dB_maxL, A.comp_of_dB, invN=invN)[:, i_cmb]
    W_dBdB_maxL = W_dBdB(A_maxL, A_dB_maxL, A_dBdB_maxL,
                         A.comp_of_dB, invN=invN)[:, :, i_cmb]
    V_maxL = np.einsum('ij,ij...->...', res.Sigma, W_dBdB_maxL)

    # Check dimentions
    assert ((n_freqs,) == W_maxL.shape == W_dB_maxL.shape[1:]
                       == W_dBdB_maxL.shape[2:] == V_maxL.shape)
    assert (len(res.params) == W_dB_maxL.shape[0] 
                            == W_dBdB_maxL.shape[0] == W_dBdB_maxL.shape[1])

    # elementary quantities defined in Stompor, Errard, Poletti (2016)
    Cl_xF = {}
    Cl_xF['yy'] = _utmv(W_maxL, Cl_fgs.T, W_maxL)  # (ell,)
    Cl_xF['YY'] = _mmm(W_dB_maxL, Cl_fgs.T, W_dB_maxL.T)  # (ell, param, param)
    Cl_xF['yz'] = _utmv(W_maxL, Cl_fgs.T, V_maxL )  # (ell,)
    Cl_xF['Yy'] = _mmv(W_dB_maxL, Cl_fgs.T, W_maxL)  # (ell, param)
    Cl_xF['Yz'] = _mmv(W_dB_maxL, Cl_fgs.T, V_maxL)  # (ell, param)

    # bias and statistical foregrounds residuals
    res.noise = Cl_noise
    res.bias = Cl_xF['yy'] + 2 * Cl_xF['yz']  # S16, Eq 23
    res.stat = np.einsum('ij, lij -> l', res.Sigma, Cl_xF['YY'])  # E11, Eq. 12
    res.var = res.stat**2 + 2 * np.einsum('li, ij, lj -> l', # S16, Eq. 28
                                          Cl_xF['Yy'], res.Sigma, Cl_xF['Yy'])

    ###############################################################################
    # 5. Plug into the cosmological likelihood
    print ('======= OPTIMIZATION OF COSMO LIKELIHOOD =======')
    Cl_fid = {}
    Cl_fid['BB'] = _get_Cl_cmb(Alens=Alens, r=r)[2][lmin:lmax+1]
    Cl_fid['BuBu'] = _get_Cl_cmb(Alens=0.0, r=1.0)[2][lmin:lmax+1]
    Cl_fid['BlBl'] = _get_Cl_cmb(Alens=1.0, r=0.0)[2][lmin:lmax+1]

    res.BB = Cl_fid['BB']*1.0
    res.BuBu = Cl_fid['BuBu']*1.0
    res.BlBl = Cl_fid['BlBl']*1.0
    res.ell = ell
    if make_figure:
        fig = pl.figure( figsize=(14,12), facecolor='w', edgecolor='k' )
        ax = pl.gca()
        left, bottom, width, height = [0.2, 0.2, 0.15, 0.2]
        ax0 = fig.add_axes([left, bottom, width, height])
        ax0.set_title(r'$\ell_{\min}=$'+str(lmin)+\
            r'$ \rightarrow \ell_{\max}=$'+str(lmax), fontsize=16)

        ax.loglog(ell, Cl_fid['BB'], color='DarkGray', linestyle='-', label='BB tot', linewidth=2.0)
        ax.loglog(ell, Cl_fid['BuBu']*r , color='DarkGray', linestyle='--', label='primordial BB for r='+str(r), linewidth=2.0)
        ax.loglog(ell, res.stat, 'DarkOrange', label='statistical residuals', linewidth=2.0)
        ax.loglog(ell, res.bias, 'DarkOrange', linestyle='--', label='systematic residuals', linewidth=2.0)
        ax.loglog(ell, res.noise, 'DarkBlue', linestyle='--', label='noise after component separation', linewidth=2.0)
        ax.legend(loc='upper right', prop={'size':15})
        ax.set_xlabel('$\ell$', fontsize=20)
        ax.set_ylabel('$C_\ell$ [$\mu \mathrm{K}^{2}$]', fontsize=20)
        ax.set_xlim(lmin,lmax)

    ## 5.1. data 
    Cl_obs = Cl_fid['BB'] + Cl_noise
    dof = (2 * ell + 1) * fsky
    YY = Cl_xF['YY']
    tr_SigmaYY = np.einsum('ij, lji -> l', res.Sigma, YY)

    ## 5.2. modeling
    def cosmo_likelihood(r_):
        # S16, Appendix C
        Cl_model = Cl_fid['BlBl'] * Alens + Cl_fid['BuBu'] * r_ + Cl_noise
        dof_over_Cl = dof / Cl_model
        ## Eq. C3
        U = np.linalg.inv(res.Sigma_inv + np.dot(YY.T, dof_over_Cl))
        
        ## Eq. C9
        first_row = np.sum(dof_over_Cl * (
            Cl_obs * (1 - np.einsum('ij, lji -> l', U, YY) / Cl_model) 
            + tr_SigmaYY))
        second_row = - np.einsum(
            'l, m, ij, mjk, kf, lfi',
            dof_over_Cl, dof_over_Cl, U, YY, res.Sigma, YY)
        trCinvC = first_row + second_row
       
        ## Eq. C10
        first_row = np.sum(dof_over_Cl * (Cl_xF['yy'] + 2 * Cl_xF['yz']))
        ### Cyclicity + traspose of scalar + grouping terms -> trace becomes
        ### Yy_ell^T U (Yy + 2 Yz)_ell'
        trace = np.einsum('li, ij, mj -> lm',
                          Cl_xF['Yy'], U, Cl_xF['Yy'] + 2 * Cl_xF['Yz'])
        second_row = - _utmv(dof_over_Cl, trace, dof_over_Cl)
        trECinvC = first_row + second_row

        ## Eq. C12
        logdetC = np.sum(dof * np.log(Cl_model)) - np.log(np.linalg.det(U))

        # Cl_hat = Cl_obs + tr_SigmaYY

        ## Bringing things together
        return trCinvC + trECinvC + logdetC


    # Likelihood maximization
    r_grid = np.logspace(-5,0,num=500)
    logL = np.array([cosmo_likelihood(r_loc) for r_loc in r_grid])
    ind_r_min = np.argmin(logL)
    r0 = r_grid[ind_r_min]
    if ind_r_min == 0:
        bound_0 = 0.0
        bound_1 = r_grid[1]
        # pl.figure()
        # pl.semilogx(r_grid, logL, 'r-')
        # pl.show()
    elif ind_r_min == len(r_grid)-1:
        bound_0 = r_grid[-2]
        bound_1 = 1.0
        # pl.figure()
        # pl.semilogx(r_grid, logL, 'r-')
        # pl.show()
    else:
        bound_0 = r_grid[ind_r_min-1]
        bound_1 = r_grid[ind_r_min+1]
    print('bounds on r = ', bound_0, ' / ', bound_1)
    print('starting point = ', r0)
    res_Lr = sp.optimize.minimize(cosmo_likelihood, [r0], bounds=[(bound_0,bound_1)], **minimize_kwargs)
    print ('    ===>> fitted r = ', res_Lr['x'])

    print ('======= ESTIMATION OF SIGMA(R) =======')
    def sigma_r_computation_from_logL(r_loc):
        THRESHOLD = 1.00
        # THRESHOLD = 2.30 when two fitted parameters
        delta = np.abs( cosmo_likelihood(r_loc) - res_Lr['fun'] - THRESHOLD )
        # print r_loc, cosmo_likelihood(r_loc),  res_Lr['fun']
        return delta

    if res_Lr['x'] != 0.0:
        sr_grid = np.logspace(np.log10(res_Lr['x']), 0, num=25)
    else:
        sr_grid = np.logspace(-5,0,num=25)

    slogL = np.array([sigma_r_computation_from_logL(sr_loc) for sr_loc in sr_grid ])
    ind_sr_min = np.argmin(slogL)
    sr0 = sr_grid[ind_sr_min]
    print('ind_sr_min = ', ind_sr_min)
    print('sr_grid[ind_sr_min-1] = ', sr_grid[ind_sr_min-1])
    print('sr_grid[ind_sr_min+1] = ', sr_grid[ind_sr_min+1])
    print('sr_grid = ', sr_grid)
    if ind_sr_min == 0:
        print('case # 1')
        bound_0 = res_Lr['x']
        bound_1 = sr_grid[1]
    elif ind_sr_min == len(sr_grid)-1:
        print('case # 2')
        bound_0 = sr_grid[-2]
        bound_1 = 1.0
    else:
        print('case # 3')
        bound_0 = sr_grid[ind_sr_min-1]
        bound_1 = sr_grid[ind_sr_min+1]
    print('bounds on sigma(r) = ', bound_0, ' / ', bound_1)
    print('starting point = ', sr0)
    res_sr = sp.optimize.minimize(sigma_r_computation_from_logL, sr0,
            bounds=[(bound_0.item(),bound_1.item())],
            # item required for test to pass but reason unclear. sr_grid has
            # extra dimension?
            **minimize_kwargs)
    print ('    ===>> sigma(r) = ', res_sr['x'] -  res_Lr['x'])
    res.cosmo_params = {}
    res.cosmo_params['r'] = (res_Lr['x'], res_sr['x']- res_Lr['x'])


    ###############################################################################
    # 6. Produce figures
    if make_figure:
        print ('======= GRIDDING COSMO LIKELIHOOD =======')
        r_grid = np.logspace(-4,-1,num=500)
        logL = np.array([ cosmo_likelihood(r_loc) for r_loc in r_grid ])
        chi2 = logL - np.min(logL)
        ax0.semilogx( r_grid,  np.exp(-chi2), color='DarkOrange', linestyle='-', linewidth=2.0, alpha=0.8 )
        ax0.axvline(x=r, color='k', linestyle='--')
        ax0.set_ylabel(r'$\mathcal{L}(r)$', fontsize=20)
        ax0.set_xlabel(r'tensor-to-scalar ratio $r$', fontsize=20)
        #pl.show()

        return res, fig

    return res

#Added by Clement Leloup
def harmonic_xForecast(components, instrument, alms_fgs, lmin, lmax, invNl=None, fsky=1.0, Alens=1.0, r=0.001, Nl=None, lite=False, make_figure=False, **minimize_kwargs):

    """ xForecast

    Run XForcast (Stompor et al, 2016) using the provided instrumental
    specifications and input foregrounds maps. If the foreground maps match the
    components provided (constant spectral indices are assumed), it reduces to
    CMB4cast (Errard et al, 2011). Currently, only polarization is considered
    fot component separation and only the BB power spectrum for cosmological
    analysis.

    Parameters
    ----------
    components: list
         `Components` of the mixing matrix
    instrument:
        Object that provides the following as a key or an attribute.

        - **frequency**
        - **depth_p** (optional, frequencies are inverse-noise
          weighted according to these noise levels)
        - **fwhm** (optional)

        They can be anything that is convertible to a float numpy array.
    alms_fgs: ndarray
        The foreground alms. No CMB. Shape `(n_freq, n_stokes, n_lm)`.
        If a mask needs to be applied, do it before calling this function.
    lmin: int
        minimum multipole entering the likelihood computation
    lmax: int
        maximum multipole entering the likelihood computation
    invNl: ndarray
        Estimated inverse noise cov matrix in harmonic domain. If None,
        computed using harmonic_noise_cov. Shape `(n_freq, n_stokes, n_lm)`.
        B_modes must be stored as last element of n_stokes dimension.
    fsky: float
        sky fraction
    Alens: float
        Amplitude of the lensing B-modes entering the likelihood on r
    r: float
        tensor-to-scalar ratio assumed in the likelihood on r
    Nl: ndarray
        true noise covariance if different than invNl^(-1)
    lite: bool
        if true, only perform component separation
    minimize_kwargs: dict
        Keyword arguments to be passed to `scipy.optimize.minimize` during
        the fitting of the spectral parameters.
        A good choice for most cases is
        `minimize_kwargs = {'tol': 1, options: {'disp': True}}`. `tol` depends
        on both the solver and your signal to noise: it should ensure that the
        difference between the best fit -logL and and the minimum is well less
        then 1, without exagereting (a difference of 1e-4 is useless).
        `disp` also triggers a verbose callback that monitors the convergence.

    Returns
    -------
    xFres: dict
        xForecast result. It includes

        - the fitted spectral parameters
        - noise-averaged post-component separation CMB power spectrum

          - noise spectrum
          - statistical residuals spectrum
          - systematic residuals spectrum

        - noise-averaged cosmological likelihood


    """
    
    # Preliminaries
    instrument = standardize_instrument(instrument) #_force_keys_as_attributes(instrument)
    
    ell_em = hp.Alm.getlm(lmax, np.arange(alms_fgs.shape[-1]))[0]
    ell_em = np.stack((ell_em, ell_em), axis=-1).reshape(-1) # For transformation into real alms

    # Transform healpy complex alms into real alms
    alms_fgs = _format_alms(alms_fgs, lmin)

    # Format the estimated inverse noise matrix
    if invNl is None:
        invNl = harmonic_noise_cov(instrument, lmax)
        invNl = np.array([[np.diag(invNl[:,st,l]) for st in np.arange(invNl.shape[1])] for l in np.arange(invNl.shape[2])])
    invNlm = np.array([invNl[l,1:,:,:] for l in ell_em]) # Here we take only polarization
    invNl = invNl[:,-1,:,:]

    #Format the true noise covariance matrix
    if Nl is not None:
        Nlm = np.array([Nl[l,1:,:,:] for l in ell_em])
    else:
        Nlm = None
        
    n_stokes = alms_fgs.shape[1]
    n_freqs = alms_fgs.shape[2]
    ell = np.arange(lmin, lmax+1)

    ############################################################################
    # 1. Component separation using the noise-free foregrounds templare
    # grab the max-L spectra parameters with the associated error bars
    print('======= ESTIMATION OF SPECTRAL PARAMETERS =======')
    A = MixingMatrix(*components)
    A_ev = A.evaluator(instrument.frequency)
    A_dB_ev = A.diff_evaluator(instrument.frequency)
    if Nl is not None:
        A_dBdB_ev = A.diff_diff_evaluator(instrument.frequency)
    else:
        A_dBdB_ev = None

    x0 = np.array([x for c in components for x in c.defaults])

    if n_stokes == 3:  # if T and P were provided, extract P
        d_comp_sep = alms_fgs[:, 1:, :]
    else:
        d_comp_sep = alms_fgs

    mask_lmin = ell_em < lmin
    d_comp_sep[mask_lmin, ...] = 0

    res = comp_sep(A_ev, d_comp_sep, invNlm, A_dB_ev, A.comp_of_dB, x0, N_true=Nlm, A_dBdB_ev=A_dBdB_ev, **minimize_kwargs)

    res.params = A.params
    res.s = res.s.T
    res.s = _r_to_c_alms(res.s)                                                                                                                                                                               
    res.A = A_ev(res.x)
    #print(res)
    
    if lite: # stop here if only want component separation
        return res
        
    A_maxL = A_ev(res.x)
    A_dB_maxL = A_dB_ev(res.x)
    A_dBdB_maxL = A.diff_diff_evaluator(instrument.frequency)(res.x)

    ############################################################################
    # 2. Estimate noise after component separation
    ### A^T N_ell^-1 A
    print('======= ESTIMATION OF NOISE AFTER COMP SEP =======')
    i_cmb = A.components.index('CMB')
    AtNA = np.linalg.inv(_mtmm(A_maxL, invNl, A_maxL))
    if Nl is None:
        Cl_noise = np.linalg.inv(_mtmm(A_maxL, invNl, A_maxL))[lmin:, i_cmb, i_cmb]
    else:
        Cl_noise = _mmm(AtNA, _mtmm(A_maxL, _mmm(invNl, Nl, invNl), A_maxL), AtNA)[lmin:, i_cmb, i_cmb]

    ############################################################################
    # 3. Compute spectra of the input foregrounds maps
    ### TODO: which size for Cl_fgs??? N_spec != 1 ? 
    print ('======= COMPUTATION OF CL_FGS =======')
    if n_stokes == 3:  
        d_spectra = alms_fgs
    else:  # Only P is provided, add T for map2alm
        d_spectra = np.zeros((alms_fgs.shape[0], 3, n_freqs), dtype=alms_fgs.dtype)
        d_spectra[:, 1:] = alms_fgs

    # Compute cross-spectra
    almBs = _r_to_c_alms(d_spectra.T)[:,2,:] # Only B-modes

    cl_out = np.array([hp.alm2cl(alm) for alm in res.s])
    Cl_fgs = np.zeros((n_freqs, n_freqs, lmax+1), dtype=alms_fgs.dtype)
    for f1 in range(n_freqs):
        for f2 in range(n_freqs):
            if f1 > f2:
                Cl_fgs[f1, f2] = Cl_fgs[f2, f1]
            else:
                Cl_fgs[f1, f2] = hp.alm2cl(almBs[f1], almBs[f2], lmax=lmax)

    Cl_fgs = Cl_fgs[..., lmin:] / fsky

    ############################################################################
    # 4. Estimate the statistical and systematic foregrounds residuals
    print('======= ESTIMATION OF STAT AND SYS RESIDUALS =======')

    W_maxL = W(A_maxL, invN=invNl)[lmin:, i_cmb, :] #Careful, in this case, W depends on ell
    W_dB_maxL = W_dB(A_maxL, A_dB_maxL, A.comp_of_dB, invN=invNl)[..., lmin:, i_cmb, :]
    W_dBdB_maxL = W_dBdB(A_maxL, A_dB_maxL, A_dBdB_maxL, A.comp_of_dB, invN=invNl)[..., lmin:, i_cmb, :]
    V_maxL = np.einsum('ij,ij...->...', res.Sigma, W_dBdB_maxL)


    #print((n_freqs,), W_maxL.shape, W_dB_maxL.shape, W_dBdB_maxL.shape, V_maxL.shape)

    # Check dimentions
    assert ((n_freqs,) == W_maxL.shape[-1:] == W_dB_maxL.shape[-1:]
            == W_dBdB_maxL.shape[-1:] == V_maxL.shape[-1:])
    assert (len(res.params) == W_dB_maxL.shape[0] 
            == W_dBdB_maxL.shape[0] == W_dBdB_maxL.shape[1])

    # format in right shape
    W_dB_maxL = np.swapaxes(W_dB_maxL, 0, 1)
    W_dBdB_maxL = np.swapaxes(W_dBdB_maxL, 0, 2)
    
    # elementary quantities defined in Stompor, Errard, Poletti (2016)
    Cl_xF = {}
    Cl_xF['yy'] = _utmv(W_maxL, Cl_fgs.T, W_maxL)  # (ell,)
    Cl_xF['YY'] = _mmm(W_dB_maxL, Cl_fgs.T, _T(W_dB_maxL))  # (ell, param, param)
    Cl_xF['yz'] = _utmv(W_maxL, Cl_fgs.T, V_maxL )  # (ell,)
    Cl_xF['Yy'] = _mmv(W_dB_maxL, Cl_fgs.T, W_maxL)  # (ell, param)
    Cl_xF['Yz'] = _mmv(W_dB_maxL, Cl_fgs.T, V_maxL)  # (ell, param)

    # bias and statistical foregrounds residuals
    res.noise = Cl_noise
    res.bias = Cl_xF['yy'] + 2 * Cl_xF['yz']  # S16, Eq 23
    res.stat = np.einsum('ij, lij -> l', res.Sigma, Cl_xF['YY'])  # E11, Eq. 12
    res.var = res.stat**2 + 2 * np.einsum('li, ij, lj -> l', # S16, Eq. 28
                                          Cl_xF['Yy'], res.Sigma, Cl_xF['Yy'])

    ###############################################################################
    # 5. Plug into the cosmological likelihood
    print ('======= OPTIMIZATION OF COSMO LIKELIHOOD =======')
    Cl_fid = {}
    Cl_fid['BB'] = _get_Cl_cmb(Alens=Alens, r=r)[2][lmin:lmax+1]
    Cl_fid['BuBu'] = _get_Cl_cmb(Alens=0.0, r=1.0)[2][lmin:lmax+1]
    Cl_fid['BlBl'] = _get_Cl_cmb(Alens=1.0, r=0.0)[2][lmin:lmax+1]

    res.BB = Cl_fid['BB']*1.0
    res.BuBu = Cl_fid['BuBu']*1.0
    res.BlBl = Cl_fid['BlBl']*1.0
    res.ell = ell
    if make_figure:
        fig = pl.figure( figsize=(14,12), facecolor='w', edgecolor='k' )
        ax = pl.gca()
        left, bottom, width, height = [0.2, 0.2, 0.15, 0.2]
        ax0 = fig.add_axes([left, bottom, width, height])
        ax0.set_title(r'$\ell_{\min}=$'+str(lmin)+\
            r'$ \rightarrow \ell_{\max}=$'+str(lmax), fontsize=16)
        ax.loglog(ell, Cl_fid['BB'], color='DarkGray', linestyle='-', label='BB tot', linewidth=2.0)
        #ax.loglog(ell, Cl_fid['BuBu']*r , color='DarkGray', linestyle='--', label='primordial BB for r='+str(r), linewidth=2.0)
        ax.loglog(ell, res.stat, 'DarkOrange', label='statistical residuals', linewidth=2.0)
        ax.loglog(ell, res.bias, 'DarkOrange', linestyle='--', label='systematic residuals', linewidth=2.0)
        ax.loglog(ell, res.noise, 'DarkBlue', linestyle='--', label='noise after component separation', linewidth=2.0)
        ax.legend(loc='upper right', prop={'size':15})
        ax.set_xlabel('$\ell$', fontsize=20)
        ax.set_ylabel('$C_\ell$ [$\mu \mathrm{K}^{2}$]', fontsize=20)
        ax.set_xlim(lmin,lmax)

    ## 5.1. data 
    Cl_obs = Cl_fid['BB'] + Cl_noise
    dof = (2 * ell + 1) * fsky
    YY = Cl_xF['YY']
    tr_SigmaYY = np.einsum('ij, lji -> l', res.Sigma, YY)

    ## 5.2. modeling
    def cosmo_likelihood(r_):
        # S16, Appendix C
        Cl_model = Cl_fid['BlBl'] * Alens + Cl_fid['BuBu'] * r_ + Cl_noise
        dof_over_Cl = dof / Cl_model
        ## Eq. C3
        U = np.linalg.inv(res.Sigma_inv + np.dot(YY.T, dof_over_Cl))
        
        ## Eq. C9
        first_row = np.sum(dof_over_Cl * (
            Cl_obs * (1 - np.einsum('ij, lji -> l', U, YY) / Cl_model) 
            + tr_SigmaYY))
        second_row = - np.einsum(
            'l, m, ij, mjk, kf, lfi',
            dof_over_Cl, dof_over_Cl, U, YY, res.Sigma, YY)
        trCinvC = first_row + second_row
       
        ## Eq. C10
        first_row = np.sum(dof_over_Cl * (Cl_xF['yy'] + 2 * Cl_xF['yz']))
        ### Cyclicity + traspose of scalar + grouping terms -> trace becomes
        ### Yy_ell^T U (Yy + 2 Yz)_ell'
        trace = np.einsum('li, ij, mj -> lm',
                          Cl_xF['Yy'], U, Cl_xF['Yy'] + 2 * Cl_xF['Yz'])
        second_row = - _utmv(dof_over_Cl, trace, dof_over_Cl)
        trECinvC = first_row + second_row

        ## Eq. C12
        logdetC = np.sum(dof * np.log(Cl_model)) - np.log(np.linalg.det(U))

        # Cl_hat = Cl_obs + tr_SigmaYY

        ## Bringing things together
        return trCinvC + trECinvC + logdetC


    # Likelihood maximization
    r_grid = np.logspace(-5,0,num=500)
    logL = np.array([cosmo_likelihood(r_loc) for r_loc in r_grid])
    ind_r_min = np.argmin(logL)
    r0 = r_grid[ind_r_min]
    if ind_r_min == 0:
        bound_0 = 0.0
        bound_1 = r_grid[1]
        # pl.figure()
        # pl.semilogx(r_grid, logL, 'r-')
        # pl.show()
    elif ind_r_min == len(r_grid)-1:
        bound_0 = r_grid[-2]
        bound_1 = 1.0
        # pl.figure()
        # pl.semilogx(r_grid, logL, 'r-')
        # pl.show()
    else:
        bound_0 = r_grid[ind_r_min-1]
        bound_1 = r_grid[ind_r_min+1]
    print('bounds on r = ', bound_0, ' / ', bound_1)
    print('starting point = ', r0)
    res_Lr = sp.optimize.minimize(cosmo_likelihood, [r0], bounds=[(bound_0,bound_1)], **minimize_kwargs)
    print ('    ===>> fitted r = ', res_Lr['x'])

    print ('======= ESTIMATION OF SIGMA(R) =======')
    def sigma_r_computation_from_logL(r_loc):
        THRESHOLD = 1.00
        # THRESHOLD = 2.30 when two fitted parameters
        delta = np.abs( cosmo_likelihood(r_loc) - res_Lr['fun'] - THRESHOLD )
        #print(r_loc, cosmo_likelihood(r_loc),  res_Lr['fun'])
        return delta

    if res_Lr['x'] != 0.0:
        sr_grid = np.logspace(np.log10(res_Lr['x']), 0, num=25)
        #sr_grid = np.logspace(np.min(np.log10(res_Lr['x']), -3.0), np.max(np.log10(res_Lr['x']), 0), num=25)
    else:
        sr_grid = np.logspace(-5,0,num=25)

    slogL = np.array([sigma_r_computation_from_logL(sr_loc) for sr_loc in sr_grid ])
    ind_sr_min = np.argmin(slogL)
    sr0 = sr_grid[ind_sr_min]
    print('ind_sr_min = ', ind_sr_min)
    print('sr_grid[ind_sr_min-1] = ', sr_grid[ind_sr_min-1])
    print('sr_grid[ind_sr_min+1] = ', sr_grid[ind_sr_min+1])
    print('sr_grid = ', sr_grid)
    if ind_sr_min == 0:
        print('case # 1')
        bound_0 = res_Lr['x']
        bound_1 = sr_grid[1]
    elif ind_sr_min == len(sr_grid)-1:
        print('case # 2')
        bound_0 = sr_grid[-2]
        bound_1 = 1.0
    else:
        print('case # 3')
        bound_0 = sr_grid[ind_sr_min-1]
        bound_1 = sr_grid[ind_sr_min+1]
    print('bounds on sigma(r) = ', bound_0, ' / ', bound_1)
    print('starting point = ', sr0)
    res_sr = sp.optimize.minimize(sigma_r_computation_from_logL, sr0,
            bounds=[(bound_0.item(),bound_1.item())],
            # item required for test to pass but reason unclear. sr_grid has
            # extra dimension?
            **minimize_kwargs)
    print ('    ===>> sigma(r) = ', res_sr['x'] -  res_Lr['x'])
    res.cosmo_params = {}
    res.cosmo_params['r'] = (res_Lr['x'], res_sr['x']- res_Lr['x'])


    ###############################################################################
    # 6. Produce figures
    #r_grid = np.logspace(-4,-3+np.log10(5),num=500)
    r_grid = np.logspace(-5,-3+np.log10(5),num=500)
    logL = np.array([cosmo_likelihood(r_loc) for r_loc in r_grid])
    res.r_grid = r_grid
    res.chi2 = logL - np.min(logL) #for plots of r likelihood
    if make_figure:
        print ('======= GRIDDING COSMO LIKELIHOOD =======')
        r_grid = np.logspace(-4,-1,num=500)
        logL = np.array([ cosmo_likelihood(r_loc) for r_loc in r_grid ])
        chi2 = logL - np.min(logL)
        ax0.semilogx( r_grid,  np.exp(-chi2), color='DarkOrange', linestyle='-', linewidth=2.0, alpha=0.8 )
        ax0.axvline(x=r, color='k', linestyle='--')
        ax0.set_ylabel(r'$\mathcal{L}(r)$', fontsize=20)
        ax0.set_xlabel(r'tensor-to-scalar ratio $r$', fontsize=20)
        #pl.show()
        return res, fig

    return res


#Added by Wang
def harmonic_xForecast_fsl(components, instrument, alms_fgs, bls_main, bls_fsl, b1_t_main, b1_t_fsl, nfix, lmin, lmax, invNl=None, fsky=1.0, Alens=1.0, r=0.001, Nl=None, lite=False, make_figure=False, **minimize_kwargs):

    """ xForecast

    Run XForcast (Stompor et al, 2016) using the provided instrumental
    specifications and input foregrounds maps. If the foreground maps match the
    components provided (constant spectral indices are assumed), it reduces to
    CMB4cast (Errard et al, 2011). Currently, only polarization is considered
    fot component separation and only the BB power spectrum for cosmological
    analysis.

    Parameters
    ----------
    components: list
         `Components` of the mixing matrix
    instrument:
        Object that provides the following as a key or an attribute.

        - **frequency**
        - **depth_p** (optional, frequencies are inverse-noise
          weighted according to these noise levels)
        - **fwhm** (optional)

        They can be anything that is convertible to a float numpy array.
    alms_fgs: ndarray
        The foreground alms. No CMB. Shape `(n_freq, n_stokes, n_lm)`.
        If a mask needs to be applied, do it before calling this function.
    lmin: int
        minimum multipole entering the likelihood computation
    lmax: int
        maximum multipole entering the likelihood computation
    invNl: ndarray
        Estimated inverse noise cov matrix in harmonic domain. If None,
        computed using harmonic_noise_cov. Shape `(n_freq, n_stokes, n_lm)`.
        B_modes must be stored as last element of n_stokes dimension.
    fsky: float
        sky fraction
    Alens: float
        Amplitude of the lensing B-modes entering the likelihood on r
    r: float
        tensor-to-scalar ratio assumed in the likelihood on r
    Nl: ndarray
        true noise covariance if different than invNl^(-1)
    lite: bool
        if true, only perform component separation
    minimize_kwargs: dict
        Keyword arguments to be passed to `scipy.optimize.minimize` during
        the fitting of the spectral parameters.
        A good choice for most cases is
        `minimize_kwargs = {'tol': 1, options: {'disp': True}}`. `tol` depends
        on both the solver and your signal to noise: it should ensure that the
        difference between the best fit -logL and and the minimum is well less
        then 1, without exagereting (a difference of 1e-4 is useless).
        `disp` also triggers a verbose callback that monitors the convergence.

    Returns
    -------
    xFres: dict
        xForecast result. It includes

        - the fitted spectral parameters
        - noise-averaged post-component separation CMB power spectrum

          - noise spectrum
          - statistical residuals spectrum
          - systematic residuals spectrum

        - noise-averaged cosmological likelihood


    """
    
    # Preliminaries
    instrument = standardize_instrument(instrument) #_force_keys_as_attributes(instrument)
    
    ell_em = hp.Alm.getlm(lmax, np.arange(alms_fgs.shape[-1]))[0]
    ell_em = np.stack((ell_em, ell_em), axis=-1).reshape(-1) # For transformation into real alms

    # Transform healpy complex alms into real alms
    alms_fgs = _format_alms(alms_fgs, lmin)

    # Format the estimated inverse noise matrix
    if invNl is None:
        invNl = harmonic_noise_cov(instrument, lmax)
        invNl = np.array([[np.diag(invNl[:,st,l]) for st in np.arange(invNl.shape[1])] for l in np.arange(invNl.shape[2])])
    invNlm = np.array([invNl[l,1:,:,:] for l in ell_em]) # Here we take only polarization
    # invNl = invNl[:,-1,:,:]

    #Format the true noise covariance matrix
    if Nl is not None:
        Nlm = np.array([Nl[l,1:,:,:] for l in ell_em])
    else:
        Nlm = None
        
    n_stokes = alms_fgs.shape[1]
    n_freqs = alms_fgs.shape[2]
    ell = np.arange(lmin, lmax+1)

    ############################################################################
    # 1. Component separation using the noise-free foregrounds templare
    # grab the max-L spectra parameters with the associated error bars
    print('======= ESTIMATION OF SPECTRAL PARAMETERS =======')

    bls_fsl_r = _format_bls(bls_fsl)
    bls_main_r = _format_bls(bls_main)


    if n_stokes == 3:  # if T and P were provided, extract P
        d_comp_sep = alms_fgs[:, 1:, :]
        bls_fsl_r = bls_fsl_r[:,1:,:]
        bls_main_r = bls_main_r[:,1:,:]
    else:
        d_comp_sep = alms_fgs

    mask_lmin = ell_em < lmin
    d_comp_sep[mask_lmin, ...] = 0

    A_tilde_ev,  A_tilde_dB_ev, comp_of_param_tilde, x0, params = _get_modified_A(components, instrument, bls_main_r, bls_fsl_r, b1_t_main, b1_t_fsl, nfix)
    if Nl is not None:
        A_dBdB_ev = _get_modified_A_dBdB(components, instrument, bls_main, bls_fsl, b1_t_main, b1_t_fsl, nfix)
    else:
        A_dBdB_ev = None

    res = comp_sep(A_tilde_ev, d_comp_sep, invNlm, A_tilde_dB_ev,comp_of_param_tilde, x0, N_true=Nlm, A_dBdB_ev=A_dBdB_ev, **minimize_kwargs)

    res.params = params

    #print(res)
    fisher = fisher_logL_dB_dB(A_tilde_ev(res.x), res.s, A_tilde_dB_ev(res.x), comp_of_param_tilde, invNlm)
    res.s = np.swapaxes(res.s, 0, 2)
    res.s[res.s == hp.UNSEEN] = 0.
    #res.s = np.asarray(res.s, order='C').view(np.complex128)
    res.s = _r_to_c_alms(res.s)                                                                                                                                                                              
    res.A = A_tilde_ev(res.x)
    print("fisher", fisher)
    res.Sigma_inv = fisher
    res.Sigma = np.linalg.inv(fisher).T

    
    if lite: # stop here if only want component separation
        return res

    bls_main = np.swapaxes(bls_main, 0, -1)
    bls_fsl = np.swapaxes(bls_fsl, 0, -1)
    A_tilde_ev,  A_tilde_dB_ev, comp_of_param_tilde, x0, params = _get_modified_A(components, instrument, bls_main, bls_fsl, b1_t_main, b1_t_fsl, nfix)
    A_tilde_dBdB_ev = _get_modified_A_dBdB(components, instrument, bls_main, bls_fsl, b1_t_main, b1_t_fsl, nfix)
    A_maxL = A_tilde_ev(res.x)
    A_dB_maxL = A_tilde_dB_ev(res.x)
    A_dBdB_maxL =  A_tilde_dBdB_ev(res.x)

    ############################################################################
    # 2. Estimate noise after component separation
    ### A^T N_ell^-1 A
    print('======= ESTIMATION OF NOISE AFTER COMP SEP =======')
    A = MixingMatrix(*components)
    i_cmb = A.components.index('CMB')
    AtNA = np.linalg.inv(_mtmm(A_maxL[lmin:,-1,...], invNl[lmin:,-1,...], A_maxL[lmin:,-1,...]))
    if Nl is None:
        Cl_noise = np.linalg.inv(_mtmm(A_maxL[lmin:,-1,...], invNl[lmin:,-1,...], A_maxL[lmin:,-1,...]))[:, i_cmb, i_cmb]
    else:
        Cl_noise = _mmm(AtNA, _mtmm(A_maxL[lmin:,-1,...], _mmm(invNl[lmin:,-1,...], Nl[lmin:,-1,...], invNl[lmin:,-1,...]), A_maxL[lmin:,-1,...]), AtNA)[:, i_cmb, i_cmb]

    ############################################################################
    # 3. Compute spectra of the input foregrounds maps
    ### TO DO: which size for Cl_fgs??? N_spec != 1 ? 
    print ('======= COMPUTATION OF CL_FGS =======')
    if n_stokes == 3:  
        d_spectra = alms_fgs
    else:  # Only P is provided, add T for map2alm
        d_spectra = np.zeros((alms_fgs.shape[0], 3, n_freqs), dtype=alms_fgs.dtype)
        d_spectra[:, 1:] = alms_fgs

    # Compute cross-spectra
    almBs = _r_to_c_alms(d_spectra.T)[:,2,:] # Only B-modes

    cl_out = np.array([hp.alm2cl(alm) for alm in res.s])
    Cl_fgs = np.zeros((n_freqs, n_freqs, lmax+1), dtype=alms_fgs.dtype)
    for f1 in range(n_freqs):
        for f2 in range(n_freqs):
            if f1 > f2:
                Cl_fgs[f1, f2] = Cl_fgs[f2, f1]
            else:
                Cl_fgs[f1, f2] = hp.alm2cl(almBs[f1], almBs[f2], lmax=lmax)

    Cl_fgs = Cl_fgs[..., lmin:] / fsky

    ############################################################################
    # 4. Estimate the statistical and systematic foregrounds residuals
    print('======= ESTIMATION OF STAT AND SYS RESIDUALS =======')
    W_maxL = W(A_maxL, invN=invNl)[lmin:,-1, i_cmb, :] #Careful, in this case, W depends on ell
    W_dB_maxL = W_dB(A_maxL, A_dB_maxL, comp_of_param_tilde, invN=invNl)[..., lmin:, -1, i_cmb, :]
    W_dBdB_maxL = W_dBdB(A_maxL, A_dB_maxL, A_dBdB_maxL, comp_of_param_tilde, invN=invNl)[..., lmin:, -1, i_cmb, :]
    V_maxL = np.einsum('ij,ij...->...', res.Sigma, W_dBdB_maxL)
    # format in right shape
    W_dB_maxL = np.swapaxes(W_dB_maxL, 0, 1)
    W_dBdB_maxL = np.swapaxes(W_dBdB_maxL, 0, 2)

    #print((n_freqs,), W_maxL.shape, W_dB_maxL.shape, W_dBdB_maxL.shape, V_maxL.shape)

    # Check dimentions
    assert ((n_freqs,) == W_maxL.shape[-1:] == W_dB_maxL.shape[-1:]
            == W_dBdB_maxL.shape[-1:] == V_maxL.shape[-1:])
    # assert (len(res.params) == W_dB_maxL.shape[0]  
    #         == W_dBdB_maxL.shape[0] == W_dBdB_maxL.shape[1])
    assert (W_dB_maxL.shape[-2] == W_dBdB_maxL.shape[-2] == W_dBdB_maxL.shape[-3]) #TODO: should include the params here
    
    # elementary quantities defined in Stompor, Errard, Poletti (2016)
    Cl_xF = {}
    Cl_xF['yy'] = _utmv(W_maxL, Cl_fgs.T, W_maxL)  # (ell,)
    Cl_xF['YY'] = _mmm(W_dB_maxL, Cl_fgs.T, _T(W_dB_maxL))  # (ell, param, param)
    Cl_xF['yz'] = _utmv(W_maxL, Cl_fgs.T, V_maxL )  # (ell,)
    Cl_xF['Yy'] = _mmv(W_dB_maxL, Cl_fgs.T, W_maxL)  # (ell, param)
    Cl_xF['Yz'] = _mmv(W_dB_maxL, Cl_fgs.T, V_maxL)  # (ell, param)

    # bias and statistical foregrounds residuals
    res.noise = Cl_noise
    res.bias = Cl_xF['yy'] + 2 * Cl_xF['yz']  # S16, Eq 23
    res.stat = np.einsum('ij, lij -> l', res.Sigma, Cl_xF['YY'])  # E11, Eq. 12
    res.var = res.stat**2 + 2 * np.einsum('li, ij, lj -> l', # S16, Eq. 28
                                          Cl_xF['Yy'], res.Sigma, Cl_xF['Yy'])

    ###############################################################################
    # 5. Plug into the cosmological likelihood
    print ('======= OPTIMIZATION OF COSMO LIKELIHOOD =======')
    Cl_fid = {}
    Cl_fid['BB'] = _get_Cl_cmb(Alens=Alens, r=r)[2][lmin:lmax+1]
    Cl_fid['BuBu'] = _get_Cl_cmb(Alens=0.0, r=1.0)[2][lmin:lmax+1]
    Cl_fid['BlBl'] = _get_Cl_cmb(Alens=1.0, r=0.0)[2][lmin:lmax+1]

    res.BB = Cl_fid['BB']*1.0
    res.BuBu = Cl_fid['BuBu']*1.0
    res.BlBl = Cl_fid['BlBl']*1.0
    res.ell = ell
    if make_figure:
        fig = pl.figure( figsize=(14,12), facecolor='w', edgecolor='k' )
        ax = pl.gca()
        left, bottom, width, height = [0.2, 0.2, 0.15, 0.2]
        ax0 = fig.add_axes([left, bottom, width, height])
        ax0.set_title(r'$\ell_{\min}=$'+str(lmin)+\
            r'$ \rightarrow \ell_{\max}=$'+str(lmax), fontsize=16)
        ax.loglog(ell, Cl_fid['BB'], color='DarkGray', linestyle='-', label='BB tot', linewidth=2.0)
        # ax.loglog(ell, Cl_fid['BuBu']*r , color='DarkGray', linestyle='--', label='primordial BB for r='+str(r), linewidth=2.0)
        ax.loglog(ell, res.stat, 'DarkOrange', label='statistical residuals', linewidth=2.0)
        ax.loglog(ell, res.bias, 'DarkOrange', linestyle='--', label='systematic residuals', linewidth=2.0)
        ax.loglog(ell, res.noise, 'DarkBlue', linestyle='--', label='noise after component separation', linewidth=2.0)
        ax.legend(loc='upper right', prop={'size':15})
        ax.set_xlabel('$\ell$', fontsize=20)
        ax.set_ylabel('$C_\ell$ [$\mu \mathrm{K}^{2}$]', fontsize=20)
        ax.set_xlim(lmin,lmax)

    ## 5.1. data 
    Cl_obs = Cl_fid['BB'] + Cl_noise
    dof = (2 * ell + 1) * fsky
    YY = Cl_xF['YY']
    tr_SigmaYY = np.einsum('ij, lji -> l', res.Sigma, YY)

    ## 5.2. modeling
    def cosmo_likelihood(r_):
        # S16, Appendix C
        Cl_model = Cl_fid['BlBl'] * Alens + Cl_fid['BuBu'] * r_ + Cl_noise
        dof_over_Cl = dof / Cl_model
        ## Eq. C3
        U = np.linalg.inv(res.Sigma_inv + np.dot(YY.T, dof_over_Cl))
        
        ## Eq. C9
        first_row = np.sum(dof_over_Cl * (
            Cl_obs * (1 - np.einsum('ij, lji -> l', U, YY) / Cl_model) 
            + tr_SigmaYY))
        second_row = - np.einsum(
            'l, m, ij, mjk, kf, lfi',
            dof_over_Cl, dof_over_Cl, U, YY, res.Sigma, YY)
        trCinvC = first_row + second_row
       
        ## Eq. C10
        first_row = np.sum(dof_over_Cl * (Cl_xF['yy'] + 2 * Cl_xF['yz']))
        ### Cyclicity + traspose of scalar + grouping terms -> trace becomes
        ### Yy_ell^T U (Yy + 2 Yz)_ell'
        trace = np.einsum('li, ij, mj -> lm',
                          Cl_xF['Yy'], U, Cl_xF['Yy'] + 2 * Cl_xF['Yz'])
        second_row = - _utmv(dof_over_Cl, trace, dof_over_Cl)
        trECinvC = first_row + second_row

        ## Eq. C12
        logdetC = np.sum(dof * np.log(Cl_model)) - np.log(np.linalg.det(U))

        # Cl_hat = Cl_obs + tr_SigmaYY

        ## Bringing things together
        return trCinvC + trECinvC + logdetC


    # Likelihood maximization
    r_grid = np.logspace(-5,0,num=500)
    logL = np.array([cosmo_likelihood(r_loc) for r_loc in r_grid])
    ind_r_min = np.argmin(logL)
    r0 = r_grid[ind_r_min]
    if ind_r_min == 0:
        bound_0 = 0.0
        bound_1 = r_grid[1]
        # pl.figure()
        # pl.semilogx(r_grid, logL, 'r-')
        # pl.show()
    elif ind_r_min == len(r_grid)-1:
        bound_0 = r_grid[-2]
        bound_1 = 1.0
        # pl.figure()
        # pl.semilogx(r_grid, logL, 'r-')
        # pl.show()
    else:
        bound_0 = r_grid[ind_r_min-1]
        bound_1 = r_grid[ind_r_min+1]
    print('bounds on r = ', bound_0, ' / ', bound_1)
    print('starting point = ', r0)
    res_Lr = sp.optimize.minimize(cosmo_likelihood, [r0], bounds=[(bound_0,bound_1)], **minimize_kwargs)
    print ('    ===>> fitted r = ', res_Lr['x'])

    print ('======= ESTIMATION OF SIGMA(R) =======')
    def sigma_r_computation_from_logL(r_loc):
        THRESHOLD = 1.00
        # THRESHOLD = 2.30 when two fitted parameters
        delta = np.abs( cosmo_likelihood(r_loc) - res_Lr['fun'] - THRESHOLD )
        #print(r_loc, cosmo_likelihood(r_loc),  res_Lr['fun'])
        return delta

    if res_Lr['x'] != 0.0:
        sr_grid = np.logspace(np.log10(res_Lr['x']), 0, num=25)
        #sr_grid = np.logspace(np.min(np.log10(res_Lr['x']), -3.0), np.max(np.log10(res_Lr['x']), 0), num=25)
    else:
        sr_grid = np.logspace(-5,0,num=25)

    slogL = np.array([sigma_r_computation_from_logL(sr_loc) for sr_loc in sr_grid ])
    ind_sr_min = np.argmin(slogL)
    sr0 = sr_grid[ind_sr_min]
    print('ind_sr_min = ', ind_sr_min)
    print('sr_grid[ind_sr_min-1] = ', sr_grid[ind_sr_min-1])
    print('sr_grid[ind_sr_min+1] = ', sr_grid[ind_sr_min+1])
    print('sr_grid = ', sr_grid)
    if ind_sr_min == 0:
        print('case # 1')
        bound_0 = res_Lr['x']
        bound_1 = sr_grid[1]
    elif ind_sr_min == len(sr_grid)-1:
        print('case # 2')
        bound_0 = sr_grid[-2]
        bound_1 = 1.0
    else:
        print('case # 3')
        bound_0 = sr_grid[ind_sr_min-1]
        bound_1 = sr_grid[ind_sr_min+1]
    print('bounds on sigma(r) = ', bound_0, ' / ', bound_1)
    print('starting point = ', sr0)
    res_sr = sp.optimize.minimize(sigma_r_computation_from_logL, sr0,
            bounds=[(bound_0.item(),bound_1.item())],
            # item required for test to pass but reason unclear. sr_grid has
            # extra dimension?
            **minimize_kwargs)
    print ('    ===>> sigma(r) = ', res_sr['x'] -  res_Lr['x'])
    res.cosmo_params = {}
    res.cosmo_params['r'] = (res_Lr['x'], res_sr['x']- res_Lr['x'])


    ###############################################################################
    # 6. Produce figures
    #r_grid = np.logspace(-4,-3+np.log10(5),num=500)
    r_grid = np.logspace(-5,-3+np.log10(5),num=500)
    logL = np.array([cosmo_likelihood(r_loc) for r_loc in r_grid])
    res.r_grid = r_grid
    res.chi2 = logL - np.min(logL) #for plots of r likelihood
    if make_figure:
        print ('======= GRIDDING COSMO LIKELIHOOD =======')
        r_grid = np.logspace(-4,-1,num=500)
        logL = np.array([ cosmo_likelihood(r_loc) for r_loc in r_grid ])
        chi2 = logL - np.min(logL)
        ax0.semilogx( r_grid,  np.exp(-chi2), color='DarkOrange', linestyle='-', linewidth=2.0, alpha=0.8 )
        ax0.axvline(x=r, color='k', linestyle='--')
        ax0.set_ylabel(r'$\mathcal{L}(r)$', fontsize=20)
        ax0.set_xlabel(r'tensor-to-scalar ratio $r$', fontsize=20)
        #pl.show()
        return res, fig

    return res

def _get_Cl_cmb(Alens=1., r=0.):
    power_spectrum = hp.read_cl(CMB_CL_FILE%'lensed_scalar')[:,:4000]
    if Alens != 1.:
        power_spectrum[2] *= Alens
    if r:
        power_spectrum += r * hp.read_cl(CMB_CL_FILE
                                         %'unlensed_scalar_and_tensor_r1')[:,:4000]
    return power_spectrum


def _get_Cl_noise(instrument, A, lmax):

    #Modified by Clement Leloup
    nl = harmonic_noise_cov(instrument, lmax)
    
    AtNA = np.einsum('fi, fl, fj -> lij', A, nl, A)
    inv_AtNA = np.linalg.inv(AtNA)
    return inv_AtNA.swapaxes(-3, -1)

#Added by Clement Leloup
def harmonic_noise_cov(instrument, lmax, bl=None):

    if bl is None:
        try:
            bl = np.array([hp.gauss_beam(np.radians(b/60.), lmax=lmax)
                           for b in instrument.fwhm])
        except AttributeError:
            bl = np.ones((len(instrument.frequency), lmax+1))
        bl = np.repeat(bl[:,np.newaxis,:], 3, axis=1)

    nl = (np.array(bl) / np.radians(instrument.depth_p/60.)[:,np.newaxis,np.newaxis])**2

    return nl
