(() => {
  "use strict";

  const dialog = document.getElementById("delete-trial-dialog");
  if (!dialog || typeof dialog.showModal !== "function" ||
      typeof HTMLFormElement.prototype.requestSubmit !== "function") return;

  const name = document.getElementById("delete-trial-name");
  const cancel = document.getElementById("delete-trial-cancel");
  const confirm = document.getElementById("delete-trial-confirm");
  let pendingForm = null;
  let opener = null;
  let authorizedForm = null;
  let submitting = false;

  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    pendingForm = null;
    if (opener?.isConnected) opener.focus();
    opener = null;
  });
  dialog.addEventListener("click", (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (!submitting && event.target === dialog &&
        (event.clientX < bounds.left || event.clientX > bounds.right ||
         event.clientY < bounds.top || event.clientY > bounds.bottom)) {
      dialog.close();
    }
  });
  // Escape must never submit a form. Once a confirmed request is sent, wait
  // for the server redirect instead of presenting an apparent cancellation.
  dialog.addEventListener("cancel", (event) => {
    if (submitting) event.preventDefault();
  });
  confirm.addEventListener("click", () => {
    if (!pendingForm || submitting) return;
    authorizedForm = pendingForm;
    pendingForm.requestSubmit();
    authorizedForm = null;
  });

  document.querySelectorAll(".sf-delete-form").forEach((form) => {
    const trigger = form.querySelector("[data-delete-trigger]");
    form.addEventListener("submit", (event) => {
      if (authorizedForm !== form || !dialog.open || submitting) {
        event.preventDefault();
        return;
      }
      submitting = true;
      confirm.disabled = true;
      cancel.disabled = true;
    });
    trigger.addEventListener("click", () => {
      if (dialog.open || submitting) return;
      pendingForm = form;
      opener = trigger;
      // Names are plain text, never interpreted as HTML.
      name.textContent = form.dataset.trialName;
      dialog.showModal();
      cancel.focus();
    });
    trigger.disabled = false;
  });
  document.getElementById("delete-unavailable").hidden = true;

  window.addEventListener("pageshow", () => {
    // A browser-back restoration must not leave the confirmation disabled.
    submitting = false;
    confirm.disabled = false;
    cancel.disabled = false;
    if (dialog.open) dialog.close();
  });
})();
