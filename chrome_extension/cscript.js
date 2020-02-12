// Content script
var injected;

// Make sure content script is not injected twice
if(!injected){
    injected = true;
    // Create a listener for messages
    chrome.runtime.onMessage.addListener(
        function(request, sender, sendResponse) {
            // listen for messages sent from background.js
            if (request.message === 'URLChange') {
                alert("URL Changed: " + request.url);
                //Check for password field in the current tab
                var x = document.querySelectorAll(`input[type="password"]`);
                if (x != undefined && x.length != 0) {
                    // If there is a password field, send a response back
                    sendResponse({url: request.url});
                }
            }
        });
}