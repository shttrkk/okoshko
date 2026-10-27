/** Reveal the existing proof without changing data, scope, or which fact is tested. */
export async function revealAncestors(locator) {
  await locator.evaluate((node) => {
    for (let parent = node.parentElement; parent; parent = parent.parentElement)
      if (parent instanceof HTMLDetailsElement) parent.open = true;
  });
  await locator.waitFor({ state: "visible" });
}
export async function openTechnicalFact(card) {
  await revealAncestors(card);
  const details = card.locator(":scope > details.fact-details");
  if (await details.count()) {
    if ((await details.getAttribute("open")) === null)
      await details.locator(":scope > summary").click();
    const proof = details.locator(":scope > details").first();
    if ((await proof.getAttribute("open")) === null)
      await proof.locator(":scope > summary").click();
  }
}
export async function blockYandex(page) {
  await page.route(/yandex\.|ya\.ru|mapillary/i, (route) => route.abort());
}
// System Chrome on macOS can retain a closed driver pipe. Bound CLI teardown;
// assertions and report writing still run before a standalone script exits.
export async function closeSmokeBrowser(browser) {
  let timer;
  try {
    await Promise.race([
      browser.close(),
      new Promise((done) => {
        timer = setTimeout(done, 5000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
