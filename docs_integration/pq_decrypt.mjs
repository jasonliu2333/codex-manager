import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { bytesToByteArrays, concat } from "@tutao/tutanota-utils";
import {
  x25519Decapsulate,
  decapsulateKyber,
  bytesToKyberPrivateKey,
  bytesToKyberPublicKey,
  kyberPublicKeyToBytes,
  hkdf,
  getKeyLengthInBytes,
  AesKeyLength,
  aesDecrypt,
  uint8ArrayToKey,
} from "./package/dist/index.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const inputPath = process.argv[2];
if (!inputPath) {
  console.error("missing input json path");
  process.exit(2);
}

const input = JSON.parse(await readFile(inputPath, "utf8"));
const wasmPath = resolve(__dirname, "liboqs.wasm");
const wasm = (await WebAssembly.instantiate(await readFile(wasmPath))).instance.exports;

const b64ToBytes = (s) => {
  if (!s) return new Uint8Array();
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = s.length % 4;
  if (pad) s += "=".repeat(4 - pad);
  return new Uint8Array(Buffer.from(s, "base64"));
};

const pubEncBucketKey = b64ToBytes(input.pubEncBucketKey_b64 || "");
const parts = bytesToByteArrays(pubEncBucketKey, 4);
const senderIdentityPubKey = parts[0];
const ephemeralPubKey = parts[1];
const kyberCipherText = parts[2];
const kekEncBucketKey = parts[3];

const x25519Priv = b64ToBytes(input.x25519_priv_b64 || "");
const x25519Pub = b64ToBytes(input.x25519_pub_b64 || "");
const kyberPrivBytes = b64ToBytes(input.kyber_priv_b64 || "");
const kyberPubBytes = b64ToBytes(input.kyber_pub_b64 || "");

const kyberPriv = bytesToKyberPrivateKey(kyberPrivBytes);
const kyberPub = bytesToKyberPublicKey(kyberPubBytes);

const eccShared = x25519Decapsulate(senderIdentityPubKey, ephemeralPubKey, x25519Priv);
const kyberShared = decapsulateKyber(wasm, kyberPriv, kyberCipherText);

const protocolVersion = Number(input.protocolVersion || 2);
const context = concat(
  senderIdentityPubKey,
  ephemeralPubKey,
  x25519Pub,
  kyberPublicKeyToBytes(kyberPub),
  kyberCipherText,
  new Uint8Array([protocolVersion]),
);

const ikm = concat(eccShared.ephemeralSharedSecret, eccShared.authSharedSecret, kyberShared);
const kekBytes = hkdf(context, ikm, new TextEncoder().encode("kek"), getKeyLengthInBytes(AesKeyLength.Aes256));
const kek = uint8ArrayToKey(kekBytes);

const bucketKey = aesDecrypt(kek, kekEncBucketKey);
process.stdout.write(Buffer.from(bucketKey).toString("base64"));
