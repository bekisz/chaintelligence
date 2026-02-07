const CONFIG = {
    CRYPTOCOMPARE_API_KEY: '' // Loaded dynamically from server.py when running via Docker
};

if (typeof module !== 'undefined') {
    module.exports = CONFIG;
}
