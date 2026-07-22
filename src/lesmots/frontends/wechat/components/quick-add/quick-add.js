// Global add-word bar: fixed at the top of every tab so a word can be
// deposited the moment it's met. Owns the spell-check flow; emits
// `wordadded` with the saved word so pages can refresh.
const api = require('../../utils/api');

Component({
  data: {
    draft: '',
    adding: false,
  },

  methods: {
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
      this.setData({ adding: true });
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
            this.setData({ adding: false });
            wx.showToast({ title: word.error, icon: 'none' });
            return;
          }
          this.setData({ draft: '', adding: false });
          wx.showToast({ title: `已存入「${word.text}」`, icon: 'success' });
          this.triggerEvent('wordadded', word);
        })
        .catch(() => {
          this.setData({ adding: false });
          wx.showToast({ title: '存入失败，请重试', icon: 'none' });
        });
    },
  },
});
