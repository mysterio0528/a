const ethers = require("ethers");
const dotenv = require("dotenv");
dotenv.config();

// // HTTP Provider url if using localhost and running own full node
// "http://localhost:8545"
// // Otherwise use Alchemy, Infura, QuickNode etc

// // WSS Provider url if using localhost and running own full node
// "ws://localhost:8546"
// // Otherwise use Alchemy, Infura, QuickNode etc

// Http Provider
const httpProviderUrl = process.env.PROVIDER_HTTP;
const provider = new ethers.providers.JsonRpcProvider(httpProviderUrl);

// WSS Provider
const wssProviderUrl = process.env.PROVIDER_WSS;
const providerWSS = new ethers.providers.WebSocketProvider(wssProviderUrl);

// Uniswap V3 Swap Contract
const addressUniswapV3 = "0xE592427A0AEce92De3Edee1F18E0157C05861564";

// ERC20 ABI
const abiERC20 = [
  "function decimals() view returns (uint8)",
  "function symbol() view returns (string)",
];

// MAIN Function
const main = async () => {
  // On Pending
  providerWSS.on("pending", async (txHash) => {

    // Get transaction
    try {
      const tx = await provider.getTransaction(txHash);

      // Ensure on Uniswap V3
      if (tx && tx.to === addressUniswapV3) {
        // Get data slice in Hex
        const dataSlice = ethers.utils.hexDataSlice(tx.data, 4);

        // Ensure desired data lenfth
        if (tx.data.length === 522) {
          // Decode data
          const decoded = ethers.utils.defaultAbiCoder.decode(
            [
              "address",
              "address",
              "uint24",
              "address",
              "uint256",
              "uint256",
              "uint256",
              "uint160",
            ],
            dataSlice
          );

          // Log decoded data
          console.log("");
          console.log("Open Transaction: ", tx.hash);
          console.log(decoded);

          // Interpret data - Contracts
          const contract0 = new ethers.Contract(decoded[0], abiERC20, provider);
          const contract1 = new ethers.Contract(decoded[1], abiERC20, provider);

          // Interpret data - Symbols
          const symbol0 = await contract0.symbol();
          const symbol1 = await contract1.symbol();

          // Interpret data - Decimals
          const decimals0 = await contract0.decimals();
          const decimals1 = await contract1.decimals();

          // Interpret data - Values
          const amountOut = Number(
            ethers.utils.formatUnits(decoded[5], decimals1)
          );

          // Interpret data - Values
          const amountInMax = Number(
            ethers.utils.formatUnits(decoded[6], decimals0)
          );

          // Readout
          console.log("symbol0: ", symbol0, decimals0);
          console.log("symbol1: ", symbol1, decimals1);
          console.log("amountOut: ", amountOut);
          console.log("amountInMax: ", amountInMax);
        }
      }
    } catch (err) {
      console.log(err);
    }
  });
};

main();
