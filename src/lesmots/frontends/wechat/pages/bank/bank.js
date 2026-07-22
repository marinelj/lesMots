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

  // Offer LLM spelling corrections before the word enters the bank; the
  // check failing (offline, no LLM) must never block the add itself.
  spellCheck(text) {
    return api.post('/api/check-spelling', { text })
      .then(res => {
        const suggestions = (res.suggestions || []).slice(0, 5);
        if (!suggestions.length) return text;
        return new Promise(resolve => {
          wx.showActionSheet({
            alertText: `「${text}」可能拼写有误`,
            itemList: [...suggestions, `按原样存入「${text}」`],
            success: r => resolve(r.tapIndex < suggestions.length
              ? suggestions[r.tapIndex] : text),
            fail: () => resolve(null), // user cancelled — don't save anything
          });
        });
      })
      .catch(() => text);
  },

  addWord() {
    const text = this.data.draft.trim();
    if (!text || this.data.adding) return;
    this.setData({ adding: true, error: '' });
    this.spellCheck(text)
      .then(chosen => {
        if (chosen === null) {
          this.setData({ adding: false });
          return null;
        }
        return api.post('/api/add', { text: chosen });
      })
      .then(word => {
        if (!word) return;
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
