"""The accounts a transaction pays to be landed early, and the fee every signature costs.

Kept apart from `txmeta.py` (the fingerprint a study fetches) so that `tx.py`, which the tape recorder runs on every
transaction, reads a transaction's bid without importing the fingerprint."""

from __future__ import annotations

SYSTEM = "11111111111111111111111111111111"
JITO_TIP_PROGRAM = "T1pyyaTNZsKv2WcRAB8oVnk93mLJw2XzjtVYqCsaHqt"
# Owned by Jito's tip-payment program (verified with getMultipleAccounts).
JITO_TIP_ACCOUNTS = frozenset({
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
})  # fmt: skip
BASE_FEE_PER_SIGNATURE = 5_000

# Tip accounts of the staked-connection relays, from each operator's own documentation (6 Nov 2026): a System transfer
# to one of these is a tip to that service. OKTech pays Nozomi, Astralane and Helius Sender on our fills, Jito rarely.
_NOZOMI = ("TEMPaMeCRFAS9EKF53Jd6KpHxgL47uWLcpFArU1Fanq", "noz3jAjPiHuBPqiSPkkugaJDkJscPuRhYnSpbi8UvC4", "noz3str9KXfpKknefHji8L1mPgimezaiUyCHYMDv1GE",
           "noz6uoYCDijhu1V7cutCpwxNiSovEwLdRHPwmgCGDNo", "noz9EPNcT7WH6Sou3sr3GGjHQYVkN3DNirpbvDkv9YJ", "nozFrhfnNGoyqwVuwPAW4aaGqempx4PU6g6D9CJMv7Z",
           "nozNVWs5N8mgzuD3qigrCG2UoKxZttxzZ85pvAQVrbP", "nozUacTVWub3cL4mJmGCYjKZTnE9RbdY5AP46iQgbPJ", "nozWCyTPppJjRuw2fpzDhhWbW355fzosWSzrrMYB1Qk",
           "nozWNju6dY353eMkMqURqwQEoM3SFgEKC6psLCSfUne", "nozc5yT15LazbLTFVZzoNZCwjh3yUtW86LoUyqsBu4L", "nozievPk7HyK1Rqy1MPJwVQ7qQg2QoJGyP71oeDwbsu",
           "noznbgwYnBLDHu8wcQVCEw6kDrXkPdKkydGJGNXGvL7", "nozpEGbwx4BcGp6pvEdAh1JoC2CQGZdU6HbNP1v2p6P", "nozrhjhkCr3zXT3BiT4WCodYCUFeQvcdUkM7MqhKqge",
           "nozrwQtWhEdrA6W8dkbt9gnUaMs52PdAv5byipnadq3", "nozxNBgWohjR75vdspfxR5H9ceC7XXH99xpxhVGt3Bb",
           "nozcpUkzLaxcu24kYW7RppB8i7hmosBEqnvvQWSFZMJ")  # fmt: skip
_HELIUS_SENDER = ("2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD", "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ", "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
                  "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE", "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or", "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey",
                  "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn", "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta", "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
                  "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF")  # fmt: skip
_ASTRALANE = ("ASTRaoF93eYt73TYvwtsv6fMWHWbGmMUZfVZPo3CRU9C", "AStRAnpi6kFrKypragExgeRoJ1QnKH7pbSjLAKQVWUum", "AStrAJv2RN2hKCHxwUMtqmSxgdcNZbihCwc1mCSnG83W",
              "AsTRADtvb6tTmrsqULQ9Wji9PigDMjhfEMza6zkynEvV", "AsTRAEoyMofR3vUPpf9k68Gsfb6ymTZttEtsAbv8Bk4d", "AsTra79FET4aCKWspPqeSFvjJNyp96SvAnrmyAxqg5b7",
              "AstrA1ejL4UeXC2SBP4cpeEmtcFPZVLxx3XGKXyCW6to", "AstrABAu8CBTyuPXpV4eSCJ5fePEPnxN8NqBaPKQ9fHR", "Astran35aiQUF57XZsmkWMtNCtXGLzs8upfiqXxth2bz",
              "astra4uejePWneqNaJKuFFA8oonqCE1sqF6b45kDMZm", "astra9xWY93QyfG6yM8zwsKsRodscjQ2uU2HKNL5prk", "astraEJ2fEj8Xmy6KLG7B3VfbKfsHXhHrNdCQx7iGJK",
              "astraRVUuTHjpwEVvNBeQEgwYx9w9CFyfxjYoobCZhL", "astraZW5GLFefxNPAatceHhYjfA1ciq9gvfEg2S47xk", "astraubkDw81n4LuutzSQ8uzHCv4BhPVhfvTcYv8SKC",
              "astrawVNP4xDBKT7rAdxrLYiTSTdqtUr63fSMduivXK", "astrazznxsGUhWShqgNtAdfrzP2G83DzcWVJDxwV9bF")  # fmt: skip
_BLOXROUTE = ("3UQUKjhMKaY2S6bjcQD6yHB7utcZt5bfarRCmctpRtUd", "FogxVNs6Mm2w9rnGL1vkARSwJxvLE8mujTv3LK8RnUhF", "bLx7MvxGaKdKL7mEbpk9tC79z6MnBSJoJkuaEAPu6Nd",
              "bLx7XBqSg3LUPVf1bRgCnkJmgVZR8QEgDJBPqcRLHvp", "bLx8KeZxinPwy6kkUgyzMLeqb2ARNsWjADG1dhSsVba", "bLxADBknoNj8WAGw2W6GBYeq848Xx6ajhaymV1YvrHm",
              "bLxAc88vRBwvcUQJEgcxNfBLvHPikY4csNsUmPeWea2", "bLxQ88oCiTsL8Xj4YWekKi1hjrgmbE3J3FFZ2xZHR3h", "bLxS7NoLuynNRJ4mCnEE2YbtwJFttYsEyp2ME7rp2yt",
              "bLxW6mCov7VEbrKc3S9tcBRcfSzRnLCbNp3Dfn3SJG5", "bLxXSGXs4mYPTC5okZXed1qzvjNwNJ48QJ82hT2V7w7", "bLxYi3vojbbB7hVzVDVTdBLVPhp7GJ3ZB3BwdK5sFXi",
              "bLxhLPgBXtUpX4b1bH3HatuMGMSKT9GnwtuCGiMSAqe", "bLxpY1mniuFW4PgkNA4JiNxoeKHFszryi6tNgyZAiAA", "bLxuETxd2tgWxBALNwPzAfHhsik4BzD3nrEBCiPNZQD",
              "bLxuL2gK5FW7xfahvwLrxLyW76vcCpNsKQY2CmnE6kV", "bLxv4Hnub7nDJWHs8s17o9bGU65Bnx6Yqp2fqtMgHmm")  # fmt: skip
# vanity-prefixed tip accounts seen on our 6 Nov OKTech buys (2,000,000 lamports = our tipAmount); operator unknown
_FLASH = ("FLaSHJNm5dWYzEgnHJWWJP5ccu128Mu61NJLxUf7mUXU", "FLAsHZTRcf3Dy1APaz6j74ebdMC6Xx4g6i9YxjyrDybR")
RELAY_TIP_ACCOUNTS: dict[str, str] = {**{a: "jito" for a in JITO_TIP_ACCOUNTS}, **{a: "nozomi" for a in _NOZOMI},
                                      **{a: "helius-sender" for a in _HELIUS_SENDER}, **{a: "astralane" for a in _ASTRALANE},
                                      **{a: "bloxroute" for a in _BLOXROUTE}, **{a: "flash" for a in _FLASH}}  # fmt: skip
