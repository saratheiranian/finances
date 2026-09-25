// Small enhancements; every page still works without JavaScript.

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    if (!confirm(form.dataset.confirm)) e.preventDefault();
  });
});

const inbox = document.getElementById("inbox-form");
if (inbox) {
  const boxes = [...inbox.querySelectorAll('input[name="ids"]')];
  const all = inbox.querySelector("[data-select-all]");
  const suggested = inbox.querySelector("[data-select-suggested]");
  const button = inbox.querySelector("[data-approve]");
  const update = () => {
    const n = boxes.filter((b) => b.checked).length;
    button.textContent = n ? `Approve ${n} selected` : "Approve selected";
    all.checked = n === boxes.length;
  };
  all.addEventListener("change", () => { boxes.forEach((b) => (b.checked = all.checked)); update(); });
  suggested.addEventListener("change", () => {
    boxes.filter((b) => b.hasAttribute("data-suggested")).forEach((b) => (b.checked = suggested.checked));
    update();
  });
  // Typing a category ticks that line, since you clearly mean to approve it.
  inbox.querySelectorAll('input[list="categories"]').forEach((input) => {
    input.addEventListener("input", () => {
      const box = input.closest("tr").querySelector('input[name="ids"]');
      box.checked = input.value.trim() !== "";
      update();
    });
  });
  boxes.forEach((b) => b.addEventListener("change", update));
}

// "Suggest the rest with AI": fill in categories for lines nothing else could.
const aiButton = document.querySelector("[data-ai-suggest]");
if (aiButton) {
  aiButton.addEventListener("click", async () => {
    const note = document.querySelector(".ai-note");
    aiButton.disabled = true;
    note.textContent = "Asking the AI…";
    try {
      const res = await fetch(aiButton.dataset.aiSuggest, { method: "POST" });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error);
      let filled = 0;
      for (const [id, category] of Object.entries(data.suggestions)) {
        const input = document.querySelector(`input[name="category_${id}"]`);
        if (!input || input.value) continue;
        input.value = category;
        input.dispatchEvent(new Event("input"));
        const tag = input.parentElement.querySelector(".source.ai");
        if (tag) { tag.hidden = false; tag.textContent = "AI suggestion: check it"; }
        filled++;
      }
      note.textContent = `Filled ${filled} of ${data.asked}. AI suggestions are ticked for you; untick any you're not sure of.`;
    } catch (err) {
      note.textContent = err.message || "Something went wrong.";
    } finally {
      aiButton.disabled = false;
    }
  });
}
