// DOM Element Inspector & Test Utility
// Used for inspecting JARVIS web interface state and DOM hierarchy

class DOMInspector {
  constructor(root = document.body) {
    this.root = root;
    this.observers = [];
  }

  // Find all interactive elements currently in viewport
  getInteractiveElements() {
    const selector = 'button, a, input, select, textarea, [role="button"], [tabindex]:not([tabindex="-1"])';
    const elements = Array.from(this.root.querySelectorAll(selector));
    return elements.map(el => ({
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      className: el.className || null,
      text: el.innerText ? el.innerText.trim().slice(0, 40) : null,
      visible: this.isVisible(el)
    }));
  }

  // Check if element is visible and rendered
  isVisible(element) {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    return style.display !== 'none' && 
           style.visibility !== 'hidden' && 
           style.opacity !== '0' &&
           element.offsetWidth > 0 && 
           element.offsetHeight > 0;
  }

  // Monitor DOM mutations for live reactive debugging
  startObserving(callback) {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach(mutation => {
        if (callback) callback(mutation);
      });
    });
    observer.observe(this.root, { childList: true, subtree: true, attributes: true });
    this.observers.push(observer);
    return observer;
  }

  // Clean up all active observers
  disconnectAll() {
    this.observers.forEach(obs => obs.disconnect());
    this.observers = [];
  }
}

// Export for module or global use
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { DOMInspector };
}
