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

        // The map is drawn in a dialog that may not be open yet, and so has no size. It fills its container whenever
        // that changes size, and centres on the venue again only when it first gets one, keeping the user's pan and
        // zoom after that.
        var centred = false;
        new ResizeObserver((entries) => {
            map.invalidateSize();
            if (!centred && entries[0].contentRect.width > 0) {
                map.setView([latInput.value, lonInput.value], 13);
                centred = true;
            }
        }).observe(document.getElementById('map'));
    }
}

if (window.L) {
    drawEventMap();
} else {
    document.addEventListener('DOMContentLoaded', drawEventMap);
}
