// A small state store: immutable snapshots, subscribers called on change.
export function createStore(initial) {
  let state = Object.freeze({ ...initial });
  const listeners = new Set();
  return {
    get() {
      return state;
    },
    set(patch) {
      state = Object.freeze({ ...state, ...patch });
      for (const listener of [...listeners]) {
        listener(state);
      }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
