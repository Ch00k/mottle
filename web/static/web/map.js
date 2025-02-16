function initMap() {
    var _map = L.map('map', {}).fitWorld();

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution:
            '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(_map);
    return _map;
}

var map = initMap();

var latInput = document.getElementById('latitude');
var lonInput = document.getElementById('longitude');
var radInput = document.getElementById('radius');

var overlay = L.layerGroup().addTo(map);

var defaultRadius = 50;

function populateMap(lat, lon, rad) {
    var marker = L.marker([lat, lon]).addTo(overlay);
    var circle = L.circle([lat, lon], {
        radius: rad * 1000,
    }).addTo(overlay);

    map.fitBounds(circle.getBounds());

    var distanceInput = document.createElement('input');
    distanceInput.id = 'distance';
    distanceInput.type = 'number';
    distanceInput.min = 1;
    distanceInput.max = 1000;
    distanceInput.step = 1;
    distanceInput.value = rad;
    distanceInput.autocomplete = 'off';

    distanceInput.addEventListener('input', (event) => {
        circle.setRadius(event.target.value * 1000);
        map.fitBounds(circle.getBounds());
        radInput.value = event.target.value;
    });

    var div = document.createElement('div');

    div.appendChild(distanceInput);
    div.append(' km');

    marker
        .bindPopup(div, {
            closeButton: false,
            autoClose: false,
            closeOnEscapeKey: false,
        })
        .openPopup();
}

L.Control.geocoder({ defaultMarkGeocode: false })
    .on('markgeocode', function (e) {
        overlay.clearLayers();
        populateMap(e.geocode.center.lat, e.geocode.center.lng, defaultRadius);

        latInput.value = e.geocode.center.lat;
        lonInput.value = e.geocode.center.lng;
    })
    .addTo(map);

if (latInput.value && lonInput.value && radInput.value) {
    populateMap(latInput.value, lonInput.value, radInput.value);
}
