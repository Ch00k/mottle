var map = L.map('map', {}).fitWorld();

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution:
        '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

var latInput = document.getElementById('latitude');
var lonInput = document.getElementById('longitude');

if (latInput.value && lonInput.value) {
    var marker = L.marker([latInput.value, lonInput.value]).addTo(map);
    map.setView([latInput.value, lonInput.value], 13);
}
