const form = document.querySelector("form");
const status = form.nextElementSibling;
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const [user, pass] = form.querySelectorAll("input:not([type=checkbox])");
  const ok = user.value.trim() && pass.value.trim();
  status.hidden = false;
  status.classList.toggle("error", !ok);
  status.textContent = ok ? "Welcome back, " + user.value.trim() + "!" : "Please enter both fields.";
});
