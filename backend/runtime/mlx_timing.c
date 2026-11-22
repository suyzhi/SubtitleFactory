/*
 * Native arm64 implementation of mlx_whisper.timing hotspots (DTW, backtrace,
 * and 1D median filter) so the frozen macOS app does not need to bundle
 * llvmlite/numba (~124 MiB) or scipy (~33 MiB).
 */

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/*
 * Run dynamic time warping matching mlx_whisper.timing.dtw_cpu + backtrace.
 *
 * Parameters:
 *   x:        contiguous float32 array of shape (n, m)
 *   n, m:     matrix dimensions (> 0)
 *   out_rows: caller-allocated int64 buffer of capacity >= (n + m)
 *   out_cols: caller-allocated int64 buffer of capacity >= (n + m)
 *
 * Returns path length (>= 0) on success, or negative error code on failure.
 */
int64_t mlx_dtw_cpu(
    const float *x,
    int64_t n,
    int64_t m,
    int64_t *out_rows,
    int64_t *out_cols
) {
    if (!x || !out_rows || !out_cols || n <= 0 || m <= 0) {
        return -1;
    }

    int64_t cols = m + 1;
    size_t grid_size = (size_t)(n + 1) * (size_t)cols;
    float *cost = (float *)malloc(grid_size * sizeof(float));
    int8_t *trace = (int8_t *)malloc(grid_size * sizeof(int8_t));
    if (!cost || !trace) {
        free(cost);
        free(trace);
        return -2;
    }

    for (size_t idx = 0; idx < grid_size; idx++) {
        cost[idx] = INFINITY;
        trace[idx] = -1;
    }

    cost[0] = 0.0f;
    for (int64_t j = 1; j <= m; j++) {
        for (int64_t i = 1; i <= n; i++) {
            float c0 = cost[(i - 1) * cols + (j - 1)];
            float c1 = cost[(i - 1) * cols + j];
            float c2 = cost[i * cols + (j - 1)];

            float c;
            int8_t t;
            if (c0 < c1 && c0 < c2) {
                c = c0;
                t = 0;
            } else if (c1 < c0 && c1 < c2) {
                c = c1;
                t = 1;
            } else {
                c = c2;
                t = 2;
            }

            cost[i * cols + j] = x[(i - 1) * m + (j - 1)] + c;
            trace[i * cols + j] = t;
        }
    }

    for (int64_t j = 0; j <= m; j++) {
        trace[j] = 2;
    }
    for (int64_t i = 0; i <= n; i++) {
        trace[i * cols] = 1;
    }

    int64_t i = n;
    int64_t j = m;
    int64_t count = 0;
    int64_t max_len = n + m;
    while (i > 0 || j > 0) {
        if (count >= max_len) {
            free(cost);
            free(trace);
            return -3;
        }
        out_rows[count] = i - 1;
        out_cols[count] = j - 1;
        count++;

        int8_t t = trace[i * cols + j];
        if (t == 0) {
            i--;
            j--;
        } else if (t == 1) {
            i--;
        } else if (t == 2) {
            j--;
        } else {
            free(cost);
            free(trace);
            return -4;
        }
    }

    /* Reverse path in place so indices are in forward order. */
    for (int64_t k = 0; k < count / 2; k++) {
        int64_t tmp_r = out_rows[k];
        out_rows[k] = out_rows[count - 1 - k];
        out_rows[count - 1 - k] = tmp_r;

        int64_t tmp_c = out_cols[k];
        out_cols[k] = out_cols[count - 1 - k];
        out_cols[count - 1 - k] = tmp_c;
    }

    free(cost);
    free(trace);
    return count;
}

/*
 * Apply an odd-width 1D median filter along the last dimension of a contiguous
 * float32 array already reflect-padded by (kernel // 2) on both ends.
 *
 * Parameters:
 *   padded: shape (outer, length + kernel - 1)
 *   dst:    shape (outer, length)
 */
int mlx_median_filter_1d(
    const float *padded,
    float *dst,
    int64_t outer,
    int64_t length,
    int64_t kernel
) {
    if (!padded || !dst || outer < 0 || length < 0 || kernel <= 0 || (kernel % 2) == 0) {
        return -1;
    }
    if (outer == 0 || length == 0) {
        return 0;
    }

    int64_t padded_stride = length + kernel - 1;
    int64_t mid = kernel / 2;

    float stack_buf[64];
    float *window = (kernel <= 64) ? stack_buf : (float *)malloc((size_t)kernel * sizeof(float));
    if (!window) {
        return -2;
    }

    for (int64_t row = 0; row < outer; row++) {
        const float *src_row = padded + row * padded_stride;
        float *dst_row = dst + row * length;
        for (int64_t col = 0; col < length; col++) {
            /* Insertion sort is optimal for tiny kernels (typically width=7). */
            window[0] = src_row[col];
            for (int64_t k = 1; k < kernel; k++) {
                float val = src_row[col + k];
                int64_t pos = k - 1;
                while (pos >= 0 && window[pos] > val) {
                    window[pos + 1] = window[pos];
                    pos--;
                }
                window[pos + 1] = val;
            }
            dst_row[col] = window[mid];
        }
    }

    if (window != stack_buf) {
        free(window);
    }
    return 0;
}
