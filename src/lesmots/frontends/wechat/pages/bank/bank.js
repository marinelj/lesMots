// 词库: the word bank — list, remove; adding lives in the global quick-add bar.
const api = require('../../utils/api');

Page({
  data: {
    words: [],
    expanded: '',
    loading: true,
    error: '',
  },

  onShow() {
    this.refresh();
  },

  refresh() {
    return api.get('/api/words')
      .then(words => this.setData({
        words: words.map(w => ({
          ...w,
          stars: '★'.repeat(w.familiarity || 1) + '☆'.repeat(5 - (w.familiarity || 1)),
        })),
        loading: false,
        error: '',
      }))
      .catch(() => this.setData({ loading: false, error: '加载失败，请下拉重试' }));
  },

  onPullDownRefresh() {
    this.refresh().then(() => wx.stopPullDownRefresh());
  },

  onWordAdded(e) {
    this.setData({ expanded: e.detail.text });
    this.refresh();
  },

  toggleExpand(e) {
    const text = e.currentTarget.dataset.text;
    this.setData({ expanded: this.data.expanded === text ? '' : text });
  },

  confirmRemove(e) {
    const word = this.data.words[e.currentTarget.dataset.index];
    wx.showModal({
      title: '删除',
      content: `把「${word.text}」移出词库？`,
      confirmText: '删除',
      confirmColor: '#dc2626',
      success: res => {
        if (!res.confirm) return;
        api.post('/api/remove-word', { text: word.text }).then(() => this.refresh());
      },
    });
  },
});
