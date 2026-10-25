// A polite live region. Clearing first makes a repeated message speak again.
export function createAnnouncer(region, wait = setTimeout) {
  let latest = 0;
  return function announce(message) {
    latest += 1;
    const mine = latest;
    region.textContent = "";
    wait(() => {
      if (mine === latest) {
        region.textContent = message;
      }
    }, 60);
  };
}
