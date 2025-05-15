What's Inside
As seen on the Crypto Wizards YT channel: https://youtu.be/aKgbSaJEMe0.

If you are looking for an algorithm that can detect arbitrage opportunities on a given exchange at lightening speed, this will certainly be of use to you. The Bellman Ford algorithm uses a graph search technique to quickly identify arbitrage opportunities.

This code package includes not only the algorithm, but also an example (to be used as an example only) connected to the Binance cryptocurrency exchange. Note how quickly this algorithm finds arbitrage opportunities based on standard price data.

IMPORTANT: This Binance implementation will not make you money out-the-box. Binance is probably the most competitive exchange there is for arbitrage so please do not use the code package hoping to get rich quick (sadly).

Regardless, if you know what the Bellman Ford algorithm is and are looking for a code pattern which provides not just single identification for you, but multiple, this will certainly be for you.

Uses
-
Blazingly fast arbitrage search built in Rust

-
Easy to follow logic

-
Easily convert into whatever programming language you like using an LLM

-
All connected Binance code already up and running

Requirements
-
Must have a basic understanding of Rust

-
Must be able to navigate code to extract the parts you need

-
Must have an understanding of arbitrage

START
What you need to get running

Ensure you have Rust, Cargo and Rustup installed:Rust installation instructions

This is not overly important, but when building this AutoGPT the developers versions are:
-
rustup 1.26.0

-
cargo 1.69.0

Step 1
Download package (skip if you are familiar with GIT)

If you are not familiar with GitHub or cloning code repos, you may find this video useful.

Download the package from GitHub using the url above into a folder of your choosing.

Tip: when you use git clone, you can specify the name of your folder when downloading. For example:

git clone https://your-package-url-above.git myproject
This will download the package content from GitHub into a folder you named 'myproject', but can name this folder anything you like.

Step 2
Adjust configurations

In the src/constants.rs file you will note a MODE setting with two true or false values both currently set to true.

The first true value, specifies that you would like any detected arbitrage opportunities saved to a csv file. The second true/false value indicates that you would like arbitrage opportunities to be traded.

Change/keep this second one to false for now, or if you have Binance API keys, continue with step 3.

Step 3
Add .env (optional)

Add a .env file to your main project folder and include the following:

BINANCE_API_KEY=ENTER YOUR KEY HERE 
BINANCE_API_SECRET=ENTER YOUR SECRET HERE
Change/keep this second one to false for now, or if you have Binance API keys, continue with step 3.

Step 4
Check your location

If you are in a location where Binance blocks API requests, you might need a VPN (currently works for example if you set your location to the UK) just for testing purposes. If you are in the US, you would need to either update the API endpoints to connect to binance.us or just use a VPN for example.

Remember that Binance was chosen as the example exchange code here, but you could plug in any exchange you like provided you are comfortable with Rust and writing the code.

Step 5
Build the package

Now you are ready to perform the test to ensure all is working.

cargo build
cargo build --release
cargo test
cargo run
CONGRATULATIONS
Well done - you made it
