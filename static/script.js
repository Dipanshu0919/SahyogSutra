const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const toggleDisplay = (el, show) => el && (el.style.display = show ? 'inline' : 'none');
const toggleElements = (showEls, hideEls) => {
    showEls.forEach(el => toggleDisplay(el, true));
    hideEls.forEach(el => toggleDisplay(el, false));
};

function showAlert(message, type = 'info', duration = 5000, showButtons = false) {
    const alertBar = $('#alertBar');
    const alertText = $('#alertText');
    const alertButtons = $('#alertButtons');
    const alertCloseBtn = $('#alertCloseBtn');
    alertText.textContent = message;
    alertBar.classList.remove('success', 'info', 'warning', 'error');
    if (type !== 'info') alertBar.classList.add(type);
    if (type === 'success' && showButtons) {
        alertButtons.style.display = 'flex';
        alertCloseBtn.style.display = 'none';
        duration = 0;
    } else {
        alertButtons.style.display = 'none';
        alertCloseBtn.style.display = 'block';
    }
    alertBar.classList.add('show');
    if (duration > 0) setTimeout(closeAlert, duration);
}

const closeAlert = () => $('#alertBar')?.classList.remove('show');

function openeventchat(eventid) {
    const drawer = $('#sideChatDrawer');
    const iframe = $('#globalChatIframe');
    iframe.src = `/group-chat/from-event/${eventid}`;
    drawer.classList.add('open');
}

function closeEventChat() {
    $('#sideChatDrawer').classList.remove('open');
    setTimeout(() => $('#globalChatIframe').src = '', 400);
}

function toggleDescription(id, action) {
    const shortDesc = $(`#desc-short-${id}`);
    const fullDesc = $(`#desc-full-${id}`);
    const moreBtn = $(`#read-more-btn-${id}`);
    const lessBtn = $(`#read-less-btn-${id}`);
    if (action === 'more') { toggleElements([fullDesc, lessBtn], [shortDesc, moreBtn]); }
    else { toggleElements([shortDesc, moreBtn], [fullDesc, lessBtn]); }
}

function filterCampaigns(searchInput) {
    const filter = searchInput.value.toLowerCase();
    const categoryWrapper = searchInput.closest('.campaign-category-wrapper');
    if (!categoryWrapper) return;
    categoryWrapper.querySelectorAll('.campaign-card').forEach(card => {
        card.classList.toggle('search-hidden', !card.innerText.toLowerCase().includes(filter));
    });
}

const contentLoaders = {
    campaigns: { loaded: false, callback: initializeCampaignListeners },
    addForm: { loaded: false, callback: initializeAddFormListeners },
    pending: { loaded: false, callback: initializePendingListeners }
};

function rerunScripts(container) {
    container.querySelectorAll('script').forEach(oldScript => {
        const newScript = document.createElement('script');
        oldScript.getAttributeNames().forEach(attr =>
            newScript.setAttribute(attr, oldScript.getAttribute(attr))
        );
        newScript.textContent = oldScript.textContent;
        oldScript.parentNode.replaceChild(newScript, oldScript);
    });
}

const loadCampaigns = () => loadContent('campaigns', '/show_campaigns', '#campaignsLoadingState', '#campaignsContent', 'loadCampaigns()');
const loadAddForm = () => loadContent('addForm', '/show_add_form', '#addFormLoadingState', '#addFormContent', 'loadAddForm()');
const loadPendingEvents = () => loadContent('pending', '/show_pending_events', '#pendingLoadingState', '#pendingContent', 'loadPendingEvents()');

function initializeCampaignListeners() {
    const campaignsSection = $('#campaigns');
    const backToAllBtn = $('#backToAllBtn');
    const allCategoryWrappers = $$('.campaign-category-wrapper');
    campaignsSection?.addEventListener('click', e => {
        if (!e.target.matches('.view-all-btn')) return;
        const categoryIdToShow = e.target.dataset.categoryId;
        allCategoryWrappers.forEach(wrapper => {
            const show = wrapper.dataset.categoryId === categoryIdToShow;
            wrapper.style.display = show ? 'block' : 'none';
            if (show) wrapper.querySelectorAll('.campaign-card.hidden').forEach(c => c.classList.remove('hidden'));
        });
        backToAllBtn.style.display = 'block';
        campaignsSection.scrollIntoView({ behavior: 'smooth' });
    });
    backToAllBtn?.addEventListener('click', () => {
        allCategoryWrappers.forEach(w => {
            w.style.display = 'block';
            w.querySelectorAll('.campaign-card').forEach((c, i) => i >= 4 && c.classList.add('hidden'));
        });
        backToAllBtn.style.display = 'none';
    });
}

function initializeAddFormListeners() {
    const form = $('#addEventForm');
    if (!form) return;
    form.addEventListener('submit', e => {
        e.preventDefault();
        e.target.dataset.url = '/addeventreq';
        handleFormSubmit(e.target, text => {
            if (text.includes('Please Login')) showSection('home');
            if (text.includes('Registered')) setTimeout(() => location.reload(), 4000);
        });
    });
    form.addEventListener('change', e => {
        const fd = new FormData();
        fd.append('field', e.target.name);
        fd.append('value', e.target.value);
        fetch('/save_draft', { method: 'POST', body: fd });
    });
}

const showSection = id => {
    const target = id || 'home';
    $$('.navlink').forEach(l => l.classList.toggle('active', l.dataset.section === target));
    $$('main > section').forEach(s => s.classList.toggle('active', s.id === target));
    window.scrollTo(0, 0);
    $('nav')?.classList.remove('active');
    if (target === 'campaigns') loadCampaigns();
    if (target === 'add') loadAddForm();
    if (target === 'pending') loadPendingEvents();
};

window.togglePasswordVisibility = id => {
    const field = $(`#${id}`);
    if (field) field.type = field.type === 'password' ? 'text' : 'password';
};

const addFormSubmitListener = (selector, url, callback) => {
    $(selector)?.addEventListener('submit', e => {
        e.preventDefault();
        e.target.dataset.url = url;
        handleFormSubmit(e.target, callback);
    });
};

let calendar;

window.toggleView = function(view) {
    const listBtn = $('#listViewBtn');
    const calBtn = $('#calendarViewBtn');
    const listContent = $('#campaignsContent');
    const calContent = $('#calendarView');
    if (!contentLoaders.campaigns.loaded) loadCampaigns();
    if (view === 'list') {
        listBtn.classList.add('active'); calBtn.classList.remove('active');
        listContent.style.display = 'block'; calContent.style.display = 'none';
    } else {
        listBtn.classList.remove('active'); calBtn.classList.add('active');
        listContent.style.display = 'none'; calContent.style.display = 'block';
        if (!calendar) { initCalendar(); } else { setTimeout(() => calendar.render(), 100); }
    }
};

function initCalendar() {
    const calendarEl = document.getElementById('calendar');
    calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: 'dayGridMonth',
        headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,listWeek' },
        events: function(info, successCallback, failureCallback) {
            fetch('/api').then(r => r.json()).then(data => {
                const events = data['active events'].map(e => ({
                    title: e.eventname,
                    start: e.eventstartdate + (e.eventstarttime ? 'T' + e.eventstarttime : ''),
                    url: '#',
                    extendedProps: { description: e.description, location: e.location }
                }));
                successCallback(events);
            }).catch(failureCallback);
        },
        eventClick: function(info) {
            info.jsEvent.preventDefault();
            showAlert(`${info.event.title}\n📍 ${info.event.extendedProps.location}\n📅 ${info.event.start.toLocaleDateString()}`, 'info');
        }
    });
    calendar.render();
}

window.changetemplate = () => fetch("/changetemplate").then(() => location.reload());
window.viewyourevents = username => fetch(`/viewyourevents/${username}`, { method: 'POST' })
    .then(() => { window.location.href = '#campaigns'; location.reload(); });
window.sendsortreq = sortby => fetch(`/setsortby/${sortby}`, { method: 'POST' })
    .then(() => { window.location.href = '#campaigns'; location.reload(); });

document.addEventListener('DOMContentLoaded', () => {
  const storedAlert = localStorage.getItem('showLanguageChangeAlert');
  if (storedAlert) {
    try {
      const alertData = JSON.parse(storedAlert);
      showAlert(alertData.message, 'success', 0, true);
      localStorage.removeItem('showLanguageChangeAlert');
    } catch (e) { console.error('Error parsing stored alert:', e); }
  }

  document.body.addEventListener('click', e => {
    if (e.target.matches('.navlink')) {
      e.preventDefault();
      showSection(e.target.dataset.section);
      location.hash = e.target.dataset.section;
    }
  });

  if (location.hash) { showSection(location.hash.slice(1)); }
  else { showSection('home'); }
  window.addEventListener('hashchange', () => showSection(location.hash.slice(1)));

  addFormSubmitListener('#loginpage', '/login', text => text.includes('Success') && location.reload());
  addFormSubmitListener('#signuppage', '/signup', text => text.includes('Success') && location.reload());
  addFormSubmitListener('#forgetpasswordpage', '/forgetpassword', text => text.includes('Success') && location.reload());

  $('.toggle-buttons')?.addEventListener('click', e => {
    if (e.target.tagName !== 'BUTTON') return;
    if (e.target.id === 'listViewBtn' || e.target.id === 'calendarViewBtn') return;
    const isLogin = e.target.id === 'login-tab-btn';
    $('#login-tab-btn').classList.toggle('active', isLogin);
    $('#signup-tab-btn').classList.toggle('active', !isLogin);
    $('#loginpage').classList.toggle('active', isLogin);
    $('#signuppage').classList.toggle('active', !isLogin);
    // Ensure forget password form is hidden
    $('#forgetpasswordpage').classList.remove('active');
    $('.toggle-buttons').style.display = 'flex';
  });

  // Forget Password Toggle Logic
  const forgetLink = $('#forget-pass-link');
  const backToLoginBtn = $('#backToLoginBtn');

  if (forgetLink) {
    forgetLink.addEventListener('click', () => {
      // Pre-fill email/username
      const loginUserVal = $('input[name="loginusername"]').value;
      if (loginUserVal) $('#forgetemail').value = loginUserVal;

      // Copy Password to New Password
      const loginPassVal = $('#loginpassword').value;
      if (loginPassVal) $('#newpassword').value = loginPassVal;

      $('#loginpage').classList.remove('active');
      $('#forgetpasswordpage').classList.add('active');
      $('.toggle-buttons').style.display = 'none';
    });
  }

  if (backToLoginBtn) {
    backToLoginBtn.addEventListener('click', () => {
      $('#forgetpasswordpage').classList.remove('active');
      $('#loginpage').classList.add('active');
      $('.toggle-buttons').style.display = 'flex';
    });
  }
  });
