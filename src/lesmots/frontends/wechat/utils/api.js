// Thin wx.request wrapper: JSON + Bearer-token auth against the LesMots API.
// Set BASE_URL to your backend (CloudBase Run domain, whitelisted in the
// Mini Program console).
const BASE_URL = 'https://YOUR-CLOUDBASE-RUN-DOMAIN';

function request(method, path, body, opts = {}) {
  return new Promise((resolve, reject) => {
    const header = { 'content-type': 'application/json' };
    if (!opts.skipAuth) {
      const token = wx.getStorageSync('lesmots_token');
      if (token) header['Authorization'] = `Bearer ${token}`;
    }
    wx.request({
      url: BASE_URL + path,
      method,
      data: body || {},
      header,
      success: res => {
        if (res.statusCode === 401 && !opts.skipAuth) {
          // session expired: re-login once, then retry
          getApp().login()
            .then(() => request(method, path, body, { ...opts, skipAuth: false }))
            .then(resolve, reject);
          return;
        }
        resolve(res.data);
      },
      fail: reject,
    });
  });
}

module.exports = {
  get: (path, opts) => request('GET', path, null, opts),
  post: (path, body, opts) => request('POST', path, body, opts),
};
