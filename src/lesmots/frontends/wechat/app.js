const api = require('./utils/api');

App({
  globalData: { loggedIn: false },

  onLaunch() {
    this.login();
  },

  login() {
    return new Promise((resolve, reject) => {
      wx.login({
        success: res => {
          api.post('/api/login', { wx_code: res.code }, { skipAuth: true })
            .then(data => {
              if (data.token) {
                wx.setStorageSync('lesmots_token', data.token);
                this.globalData.loggedIn = true;
                resolve(data);
              } else {
                reject(data.error || 'login failed');
              }
            })
            .catch(reject);
        },
        fail: reject,
      });
    });
  },
});
