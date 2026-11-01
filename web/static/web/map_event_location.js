// Leaflet loads deferred, so on a full page load the map is drawn once the document is parsed
function drawEventMap() {
    var map = L.map('map', {}).fitWorld();

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        // OpenStreetMap's tile servers refuse requests that do not name the site using them
        referrerPolicy: 'strict-origin-when-cross-origin',
        attribution:
            '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    var latInput = document.getElementById('latitude');
    var lonInput = document.getElementById('longitude');

    if (latInput.value && lonInput.value) {
        var marker = L.marker([latInput.value, lonInput.value]).addTo(map);
        map.setView([latInput.value, lonInput.value], 13);

        // The map is drawn in a dialog that may not be open yet, and so has no size; once it has one, the map fills
        // it and centres on the venue again
        new ResizeObserver(() => {
            map.invalidateSize();
            map.setView([latInput.value, lonInput.value], 13);
        }).observe(document.getElementById('map'));
    }
}

if (window.L) {
    drawEventMap();
} else {
    document.addEventListener('DOMContentLoaded', drawEventMap);
}
