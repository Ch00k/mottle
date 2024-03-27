function toggleCheckboxGroup(source, album_type) {
    checkboxes = document.querySelectorAll(`.${album_type}`);
    for (checkbox of checkboxes) {
        checkbox.checked = source.checked;
    }
}

function toggleSubmitButton() {
    checkboxes = document.querySelectorAll('.toggling');
    isChecked = Array.prototype.slice.call(checkboxes).some(x => x.checked);
    document.getElementById('create').disabled = true;
    if (isChecked) {
        document.getElementById('create').disabled = false;
    }
}

function toggleSubmitButtonInput(inputId, buttonId) {
    if (document.getElementById(inputId).value === "") {
        document.getElementById(buttonId).disabled = true;
    } else {
        document.getElementById(buttonId).disabled = false;
    }
}
