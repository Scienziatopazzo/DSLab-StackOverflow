#cython: language_level=3
#cython: boundscheck=False
#cython: wraparound=False
#cython: cdivision=True

from cython.operator cimport preincrement as inc, predecrement as dec
from libc.stdlib cimport malloc, free


cdef extern from "gamma.c":
    double lda_lgamma(double x) nogil


cdef double lgamma(double x) nogil:
    if x <= 0:
        with gil:
            raise ValueError("x must be strictly positive")
    return lda_lgamma(x)


cdef int searchsorted(double* arr, int length, double value) nogil:
    """Bisection search (c.f. numpy.searchsorted)
    Find the index into sorted array `arr` of length `length` such that, if
    `value` were inserted before the index, the order of `arr` would be
    preserved.
    """
    cdef int imin, imax, imid
    imin = 0
    imax = length
    while imin < imax:
        imid = imin + ((imax - imin) >> 1)
        if value > arr[imid]:
            imin = imid + 1
        else:
            imax = imid
    return imin


def _sample_topics(int[:] WS, int[:] TS, int[:] DS, int[:] ZS, int[:, :] nzw, int[:, :] nzt, int[:, :] ndz, int[:] nz,
                   double[:] alpha, double[:] eta, double[:] gamma, double[:] rands):
    cdef int i, k, w, t, d, z, z_new
    cdef double r, dist_cum
    cdef int N = WS.shape[0]
    cdef int n_rand = rands.shape[0]
    cdef int n_topics = nz.shape[0]
    cdef double eta_sum = 0
    cdef double gamma_sum = 0
    cdef double* dist_sum = <double*> malloc(n_topics * sizeof(double))
    if dist_sum is NULL:
        raise MemoryError("Could not allocate memory during sampling.")
    with nogil:
        for i in range(eta.shape[0]):
            eta_sum += eta[i]
        for i in range(gamma.shape[0]):
            gamma_sum += gamma[i]

        for i in range(N):
            w = WS[i]
            t = TS[i]
            d = DS[i]
            z = ZS[i]

            dec(nzw[z, w])
            dec(nzt[z, t])
            dec(ndz[d, z])
            dec(nz[z])

            dist_cum = 0
            for k in range(n_topics):
                # eta is a double so cdivision yields a double
                dist_cum += ((nzw[k, w] + eta[w]) / (nz[k] + eta_sum)) * ((nzt[k, t] + gamma[t]) / (nz[k] + gamma_sum)) * (ndz[d, k] + alpha[k])
                dist_sum[k] = dist_cum

            r = rands[i % n_rand] * dist_cum  # dist_cum == dist_sum[-1]
            z_new = searchsorted(dist_sum, n_topics, r)

            ZS[i] = z_new
            inc(nzw[z_new, w])
            inc(nzt[z_new, t])
            inc(ndz[d, z_new])
            inc(nz[z_new])

        free(dist_sum)


cpdef double _loglikelihood(int[:, :] nzw, int[:,:] nzt, int[:, :] ndz, int[:] nz, int[:] nd, double alpha, double eta, double gamma) nogil:
    cdef int k, d
    cdef int D = ndz.shape[0]
    cdef int n_topics = ndz.shape[1]
    cdef int vocab_size = nzw.shape[1]
    cdef int tags_vocab_size = nzt.shape[1]

    cdef double ll = 0

    cdef double lgamma_eta, lgamma_alpha, lgamma_gamma
    with nogil:
        lgamma_eta = lgamma(eta)
        lgamma_alpha = lgamma(alpha)
        lgamma_gamma = lgamma(gamma)

        # calculate log p(w|z)
        ll += n_topics * lgamma(eta * vocab_size)
        for k in range(n_topics):
            ll -= lgamma(eta * vocab_size + nz[k])
            for w in range(vocab_size):
                # if nzw[k, w] == 0 addition and subtraction cancel out
                if nzw[k, w] > 0:
                    ll += lgamma(eta + nzw[k, w]) - lgamma_eta

        # calculate log p(t|z)
        ll += n_topics * lgamma(gamma * tags_vocab_size)
        for k in range(n_topics):
            ll -= lgamma(gamma * tags_vocab_size + nz[k])
            for t in range(tags_vocab_size):
                # if nzt[k, t] == 0 addition and subtraction cancel out
                if nzt[k, t] > 0:
                    ll += lgamma(gamma + nzt[k, t]) - lgamma_gamma

        # calculate log p(z)
        for d in range(D):
            ll += (lgamma(alpha * n_topics) -
                    lgamma(alpha * n_topics + nd[d]))
            for k in range(n_topics):
                if ndz[d, k] > 0:
                    ll += lgamma(alpha + ndz[d, k]) - lgamma_alpha
        return ll
