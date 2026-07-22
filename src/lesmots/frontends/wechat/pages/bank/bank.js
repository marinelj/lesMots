// 词库: the word bank — list, add, remove. First usable page of M2 Phase 1.
const api = require('../../utils/api');

Page({
  data: {
    words: [],
    draft: '',
    expanded: '',
    loading: true,
    adding: false,
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

  onDraft(e) {
    this.setData({ draft: e.detail.value });
  },

  addWord() {
    const text = this.data.draft.trim();
    if (!text || this.data.adding) return;
    this.setData({ adding: true, error: '' });
    api.post('/api/add', { text })
      .then(word => {
        if (word.error) {
          this.setData({ error: word.error, adding: false });
          return;
        }
        this.setData({ draft: '', adding: false, expanded: word.text });
        return this.refresh();
      })
      .catch(() => this.setData({ adding: false, error: '存入失败，请重试' }));
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
