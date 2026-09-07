// Marvin Darvis — Personal Site
// Small progressive-enhancement script: mobile nav toggle + form status messaging.
// No external dependencies. Safe to defer-load.

document.addEventListener('DOMContentLoaded', function () {
  /* ---- Mobile nav toggle ---- */
  var toggle = document.querySelector('.nav-toggle');
  var links = document.querySelector('.nav-links');

  if (toggle && links) {
    toggle.addEventListener('click', function () {
      var isOpen = links.classList.toggle('open');
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    // Close the menu after a link is tapped (mobile).
    links.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        links.classList.remove('open');
        toggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  /* ---- Set active nav link based on current page ---- */
  var here = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-links a[href]').forEach(function (a) {
    var href = a.getAttribute('href');
    if (href === here || (here === '' && href === 'index.html')) {
      a.setAttribute('aria-current', 'page');
    }
  });

  /* ---- Footer year ---- */
  var yearEl = document.querySelector('[data-year]');
  if (yearEl) { yearEl.textContent = new Date().getFullYear(); }

  /* ---- Lightweight form status handling ----
     These forms post to Formspree (see README for setup). This just gives
     visitors feedback without a page reload, using the fetch + FormData API.
     If a form's action still contains "your-form-id", we skip the fetch
     and let it fall through with a helpful console note — wire up your
     endpoint per the README before relying on this in production. */
  document.querySelectorAll('form[data-async]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      var action = form.getAttribute('action') || '';
      var status = form.querySelector('.form-status');

      if (action.indexOf('your-form-id') !== -1) {
        // Not configured yet — let the user know in the UI instead of failing silently.
        e.preventDefault();
        if (status) {
          status.textContent = 'This form needs a Formspree endpoint before it can send. See README.md → "Wiring up the forms".';
          status.classList.add('visible');
        }
        return;
      }

      e.preventDefault();
      var data = new FormData(form);
      if (status) {
        status.textContent = 'Sending…';
        status.classList.add('visible');
      }

      fetch(action, {
        method: 'POST',
        body: data,
        headers: { Accept: 'application/json' }
      })
        .then(function (response) {
          if (response.ok) {
            form.reset();
            if (status) {
              status.textContent = 'Thanks — your message is in. I\'ll get back to you soon.';
              status.classList.add('success');
            }
          } else {
            if (status) {
              status.textContent = 'Something went wrong sending that. Try again, or email directly.';
            }
          }
        })
        .catch(function () {
          if (status) {
            status.textContent = 'Network error — please try again, or email directly.';
          }
        });
    });
  });
});
