function toggleCheckboxGroup(source, album_type) {
    checkboxes = document.querySelectorAll(`.${album_type}`);
    for (checkbox of checkboxes) {
        checkbox.checked = source.checked;
    }
}

function toggleSubmitButton() {
    checkboxes = document.querySelectorAll('.toggling');
    isChecked = Array.prototype.slice.call(checkboxes).some((x) => x.checked);
    document.getElementById('create').disabled = true;
    if (isChecked) {
        document.getElementById('create').disabled = false;
    }
}

function toggleSubmitButtonInput(inputId, buttonId) {
    if (document.getElementById(inputId).value === '') {
        document.getElementById(buttonId).disabled = true;
    } else {
        document.getElementById(buttonId).disabled = false;
    }
}

function toggleInput(select, selectedOption, inputId) {
    inputElem = document.getElementById(inputId);
    if (select.value == selectedOption) {
        inputElem.style.display = 'block';
        inputElem.required = true;
    } else {
        inputElem.style.display = 'none';
        inputElem.value = '';
        inputElem.required = false;
    }
}

function toggleRelatedCheckbox(checkbox, relatedCheckboxId) {
    relatedCheckbox = document.getElementById(relatedCheckboxId);

    if (checkbox.checked) {
        relatedCheckbox.disabled = false;
    } else {
        relatedCheckbox.disabled = true;
        relatedCheckbox.checked = false;
    }
}

// https://stackoverflow.com/a/49041392
function getCellValue(tr, idx) {
    return tr.children[idx].innerText || tr.children[idx].textContent;
}

function comparer(idx, asc) {
    return function (a, b) {
        return (function (v1, v2) {
            return v1 !== '' && v2 !== '' && !isNaN(v1) && !isNaN(v2)
                ? v1 - v2
                : v1.toString().localeCompare(v2);
        })(getCellValue(asc ? a : b, idx), getCellValue(asc ? b : a, idx));
    };
}

function sortTable(th) {
    table = th.closest('table');
    tbody = table.querySelector('tbody');
    Array.from(tbody.querySelectorAll('tr'))
        .sort(
            comparer(
                Array.from(th.parentNode.children).indexOf(th),
                (this.asc = !this.asc)
            )
        )
        .forEach((tr) => tbody.appendChild(tr));
}
