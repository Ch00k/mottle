// Behaviour shared by every page. Elements opt in through data attributes, so content HTMX swaps in works the same
// as the page it lands on.

// Dialogs: a <dialog class="modal"> that HTMX adds to the page opens at once, and leaves the page when it closes
document.addEventListener('htmx:load', (event) => {
    const element = event.detail.elt;
    const dialogs = element.matches?.('dialog.modal') ? [element] : element.querySelectorAll?.('dialog.modal') || [];

    for (const dialog of dialogs) {
        if (dialog.open || dialog.id === 'confirm-dialog') {
            continue;
        }
        dialog.addEventListener('close', () => dialog.remove());
        dialog.showModal();

        // Without an element of its own to focus, the dialog takes the focus itself, not its close button
        if (!dialog.querySelector('[autofocus]')) {
            dialog.focus();
        }
    }

    initDependentControls(element);
});

// A click on a dialog's backdrop, which is the dialog element itself, closes it
document.addEventListener('click', (event) => {
    if (event.target instanceof HTMLDialogElement && event.target.classList.contains('modal')) {
        event.target.close();
    }
});

// After a successful request, an element with data-close-dialog closes the dialog it is in, and one with
// data-success-redirect goes to the URL the attribute names
document.addEventListener('htmx:afterRequest', (event) => {
    if (!event.detail.successful) {
        return;
    }

    const element = event.detail.elt;

    if (element.closest('[data-close-dialog]')) {
        element.closest('dialog')?.close();
    }

    const redirect = element.dataset?.successRedirect;
    if (redirect) {
        window.location.assign(redirect);
    }
});

// Toasts: views send an "HXToast" event with a type ("success" or "error") and a body
// Lucide's check and x icons
const TOAST_ICONS = {
    success: '<path d="M20 6 9 17l-5-5" />',
    error: '<path d="M18 6 6 18" /><path d="m6 6 12 12" />',
};

function showToast(type, body) {
    const container = document.getElementById('toasts');
    const toast = document.createElement('div');
    const colours = type === 'error' ? 'bg-danger-soft text-danger' : 'bg-success-soft text-success';

    toast.className = 'toast';
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toast.innerHTML = `
        <span class="toast-icon ${colours}">
            <svg class="size-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"
                 stroke-linecap="round" stroke-linejoin="round">${TOAST_ICONS[type] || TOAST_ICONS.success}</svg>
        </span>
        <span class="flex-1"></span>`;
    toast.querySelector('.flex-1').textContent = body;
    container.appendChild(toast);

    // Showing the popover again puts it above a dialog opened since
    if (container.matches(':popover-open')) {
        container.hidePopover();
    }
    container.showPopover();

    setTimeout(() => {
        toast.dataset.leaving = '';
        setTimeout(() => {
            toast.remove();
            if (!container.hasChildNodes()) {
                container.hidePopover();
            }
        }, 300);
    }, type === 'error' ? 6000 : 3500);
}

document.addEventListener('HXToast', (event) => showToast(event.detail.type, event.detail.body));

document.addEventListener('htmx:sendError', () => showToast('error', 'Could not reach mottle. Check your connection.'));

// A failed request whose response sends no toast of its own still tells the user
document.addEventListener('htmx:responseError', (event) => {
    if (!event.detail.xhr.getResponseHeader('HX-Trigger')) {
        showToast('error', 'Something went wrong. Please try again.');
    }
});

// Confirmations: hx-confirm asks in a dialog of the app's own
document.addEventListener('htmx:confirm', (event) => {
    if (!event.detail.question) {
        return;
    }

    event.preventDefault();

    const dialog = document.getElementById('confirm-dialog');
    const danger = event.detail.elt.closest('[data-danger]') !== null;
    const confirmButton = dialog.querySelector('[value="confirm"]');

    dialog.querySelector('[data-question]').textContent = event.detail.question;
    confirmButton.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
    confirmButton.textContent = danger ? 'Remove' : 'Confirm';
    dialog.returnValue = '';

    dialog.addEventListener(
        'close',
        () => {
            if (dialog.returnValue === 'confirm') {
                event.detail.issueRequest(true);
            }
        },
        { once: true },
    );
    dialog.showModal();
});

// Progress bar: shown along the top edge while any request is in flight
let requestsInFlight = 0;

document.addEventListener('htmx:beforeRequest', () => {
    requestsInFlight += 1;
    document.body.classList.add('is-loading');
});

document.addEventListener('htmx:afterRequest', () => {
    requestsInFlight = Math.max(0, requestsInFlight - 1);
    if (requestsInFlight === 0) {
        document.body.classList.remove('is-loading');
    }
});

// Menus (<details class="menu">) close on a click outside them, on a click on one of their items, and on Escape
document.addEventListener('click', (event) => {
    for (const menu of document.querySelectorAll('details.menu[open]')) {
        if (!menu.contains(event.target) || event.target.closest('.menu-item')) {
            menu.open = false;
        }
    }
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        document.querySelectorAll('details.menu[open]').forEach((menu) => (menu.open = false));
    }
});

// Dependent controls:
// - data-enables="<id>" on a checkbox disables (and unchecks) the control with that id while it is unchecked
// - data-shows="<id>" on a radio or checkbox shows the element with that id only while it is checked
// - data-check-all="<group>" on a checkbox checks the boxes with data-group="<group>"
// - data-requires-checked="<selector>" on a button disables it while no box matching the selector is checked
function initDependentControls(root) {
    root.querySelectorAll?.('[data-enables], [data-shows]').forEach(syncDependent);
    root.querySelectorAll?.('[data-check-all]').forEach(syncCheckAll);
    root.querySelectorAll?.('[data-requires-checked]').forEach(syncRequiresChecked);
}

function syncDependent(control) {
    if (control.dataset.enables) {
        const target = document.getElementById(control.dataset.enables);
        target.disabled = !control.checked;
        if (!control.checked) {
            target.checked = false;
        }
    }

    if (control.dataset.shows) {
        // Radios in a group each show their own element, so every radio of the group is synced
        const controls = control.type === 'radio' ? document.getElementsByName(control.name) : [control];
        for (const c of controls) {
            const target = c.dataset.shows && document.getElementById(c.dataset.shows);
            if (target) {
                target.hidden = !c.checked;
            }
        }
    }
}

function syncCheckAll(master) {
    const boxes = [...document.querySelectorAll(`[data-group="${master.dataset.checkAll}"]`)];
    const checked = boxes.filter((box) => box.checked).length;
    master.checked = boxes.length > 0 && checked === boxes.length;
    master.indeterminate = checked > 0 && checked < boxes.length;
}

function syncRequiresChecked(button) {
    button.disabled = document.querySelector(`${button.dataset.requiresChecked}:checked`) === null;
}

document.addEventListener('change', (event) => {
    const control = event.target;

    if (control.dataset.enables || control.dataset.shows) {
        syncDependent(control);
    }

    if (control.dataset.checkAll) {
        document
            .querySelectorAll(`[data-group="${control.dataset.checkAll}"]`)
            .forEach((box) => (box.checked = control.checked));
    }

    if (control.dataset.group) {
        document.querySelectorAll(`[data-check-all="${control.dataset.group}"]`).forEach(syncCheckAll);
    }

    document.querySelectorAll('[data-requires-checked]').forEach(syncRequiresChecked);
});

// Expanding: a button with data-toggle-details opens every <details> in its dialog, or closes them all if any is open
document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-toggle-details]');
    if (!button) {
        return;
    }

    const groups = [...button.closest('dialog').querySelectorAll('details.group')];
    const open = !groups.some((group) => group.open);
    groups.forEach((group) => (group.open = open));
    button.querySelector('[data-label]').textContent = open ? 'Collapse all' : 'Expand all';
});

// Sorting: a click on a table header with data-sort sorts the rows by that column, by the cells' data-value when they
// have one, and by their text otherwise
document.addEventListener('click', (event) => {
    const header = event.target.closest('th[data-sort]');
    if (!header) {
        return;
    }

    const table = header.closest('table');
    const tbody = table.querySelector('tbody');
    const index = [...header.parentNode.children].indexOf(header);
    const ascending = header.dataset.direction !== 'asc';

    table.querySelectorAll('th[data-sort]').forEach((th) => delete th.dataset.direction);
    header.dataset.direction = ascending ? 'asc' : 'desc';

    const value = (row) => {
        const cell = row.children[index];
        return cell.dataset.value ?? cell.textContent.trim();
    };

    [...tbody.rows]
        .sort((a, b) => {
            const [x, y] = ascending ? [value(a), value(b)] : [value(b), value(a)];
            return x.localeCompare(y, undefined, { numeric: true, sensitivity: 'base' });
        })
        .forEach((row) => tbody.appendChild(row));
});

// Search tabs: switch between the result lists without a request
document.addEventListener('click', (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab) {
        return;
    }

    const tablist = tab.closest('[role="tablist"]');
    for (const t of tablist.querySelectorAll('[role="tab"]')) {
        const selected = t === tab;
        t.setAttribute('aria-selected', String(selected));
        document.getElementById(t.getAttribute('aria-controls')).hidden = !selected;
    }
});
