"""Install the LoomHarness skill and its lifecycle hooks on Windows, macOS or Linux.

Usage: python -X utf8 install.py [--cli codex|claude|gemini|antigravity|all]

By default, register hooks for Codex CLI. --cli all registers hooks for
supported clients with existing configuration directories on this computer.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile


FILES = ("references/helper.md", "references/context.md", "references/verification.md", "agents/openai.yaml",
         "scripts/harness.py", "scripts/context.py", "scripts/build_check.py", "scripts/symbols.py",
         "scripts/view.py", "scripts/plan.py", "scripts/cli_adapters.py",
         "scripts/ssh_windows.py",
         "scripts/javac_cache.py", "scripts/team.py", "scripts/team_flow.py", "scripts/team_usage.py", "scripts/initiative.py",
         "scripts/team_driver.py", "scripts/model_policy.py", "scripts/history.py", "references/history.md",
         "scripts/inbox.py", "scripts/metrics.py", "scripts/records.py", "scripts/review_queue.py", "scripts/team_models.py", "scripts/record_wait.js", "scripts/hk.py", "scripts/hk_call.py", "scripts/self_update.py", "scripts/mcp_server.py",
         "references/team.md", "references/organization.md", "references/initiative.md", "references/adaptive-team.md",
         "references/review.md", "references/delivery.md", "references/executor.md",
         "references/telemetry.md", "references/commands.md", "SKILL.md", "VERSION")
# Only replace known, unmodified versions from earlier releases.
PREVIOUS_RELEASES = {
    "scripts/model_policy.py": {"9816ed1220283a3ed5134ca41117d7c290c95f99d1e9a19166639fd9cf1f7c99", "a46df1815dfeb3dc7ae441e94482db313b8138527601eb417a30bbd7baa2e59b"},
    "scripts/records.py": {"381aed416ab85e7ae32a3ffb68ecacac0012aec3f6d5ba9b5da867e9dda8b00d"},
    "references/helper.md": {"0a0ac95c21d6b0d30d999c324b697beaabb6ed440786acd2f66d22bdfdaa278f", "63eee6f7d8c5e36b8185d784c23821f0435ef1c905473a6a834c1ced119b6df1", "258eed8adc5d5485bb1c20aff5966f039137f1027513b738a1dd3b42de832560", "e6fc6b2bc2d059e79036e00f4954819c6bbfab051be33d758b8aea7744e764df", "a7dc383f6a815688ca058df15717cb58e8c7d92bef918bb831fe54c42048e4ff", "0ea93631ee71dea07b516e880d796706b5232afae93b6c6e26d70750276d8e97",
                            "465556f052a223acd8377a6c49722a7e7f31d3d90c94822ccc19d2dbe0ec084e",
                            "e73995cf2977327a66c47025db133a12df40d1c26ada7ebb23f1eb9c91c8d8b7",
                            "f9036665c6a0749bd1cfbd7d385e35e47a3a4525ed1c2d67bacb5e16cbb3091a"},
    "references/context.md": {"d5290b74ef3b2a948dc3b71d3b07fbf8027acf1af7ea125335bf9a6210f2f36c",
                              "1f165cc57f612eeb1597bbe8e2e594e3d6ef1088aacfb4cfe6c31d01135bc568", "89f5ecda9f577b82796eba1fca2a91fbd133b9c2f355c1872358548b074413cb", "8165bb7688d2b16b086c31e567e52127b6773d9ad88cb6a4d064f5bd4d94aff9", "6c8cd9be38e874e21c38b1b5c400daf6cd7bcb6ef7e3ac25f47418599698de78", "c7efde624ad46f30496f4eb7ba858667b4c693ade7be23b4424f64a2f6501fa6", "9cdb8f467a73b059544e17e48c03255aefe1a4e51cbfbf941c49153d94c4ee2f", "6407e901f34c5dabdcadf1542890416c0123821aaa9d183bbc08afd937e013c5",
                             "66442f682f9ffbe2e6d556facd964a3e2232d1ed71b4d736437f3ae63d0d1be4",
                             "6f3c6f48939bf21eab371ae0155db3a6538db28cf3b464163885adc254ebdade",
                             "965aa09757bd603716f1b822c82ba80119d3d7af181897ef9aa5ebadd565ee9e",
                             "ae017733874c1382bf66b73d33a2f86082511ab78515728b9aa28f0b848a0c16",
                             "da5a4e06ee3c787a4e4d078dc6e2cf4b932a092a3c67583a7651cc272491590d"},
    "references/verification.md": {"bfcc38c062277319ee2c3277c6bb3e2b8e9126d50c41698fffa3158281f1294c", "555c1b90749a4c55de1c223e62d51337d59f85b3608cf5b66b7611c0e9c4e623", "003dddf83eda40addc47a52a6fc54112aa3b62a9fe5ee62a0334c30a62162701",
                                  "183c6eea961ffb38696be3c67c073abffc62b91ea0261cc626f12ecdec494190",
                                  "c0470693f63aa29bef2057a7fdd32f3b11cabb21a3d448683d5ac73ad645d0ae"},
    "agents/openai.yaml": {"b7a543d351c4fa21113c00c2ac668e659c127e1626df43b93fccc118af431e90", "c4add2e8187fe46b3e8acca5bc6ae976c98944ee6792806f8055519b7d53c83f", "9a562933e95b99076c31db0c0c2b1cd8a3ed2d7e2a5e1870dcb77d6eb06fdb50", "b062ab88ac87ae418a1e86929b09b5d89e77abd30f3417efc0ca8cf0f871b5b5", "8fbcae54d22245912bd595847aa17417c20ae374b8367bf4a03ee01afe67ccd1",
                          "de0e5cf137c573e71fc0f592b9c3627e922f521d21b8f6d86748c03e8532d3df"},
    "scripts/harness.py": {"67942324e4bab3d99985afc4944acbcc083c7f479dc832ded436eda59d40afd4", "ee6625c358cb5c25ffbd68ca00196ae31466e18b906b3755592a96df07b0071b", "4dd55626bc5a758bb6d217bcb2e136cfc41088946c4ae18f6cda8c6c912c54c5", "3a79b1d1312137921e95e1448013492730081cf74d6fd9fafbc3fb08b9c5bd95", "9a5cbaada3a941c90cbf6b0a37ee99fb25d0e32cd1908dcb2be029f760dc867f", "8965c6256e7c49d52556b4431987642d6efd83ea46b07b3fcd9eb4f2fff29655", "db0393519c46f9ce7ea4b6e032c7c4248b86b7698163b63ce38a5c49b9950015", "63856042f45d146b8bbeaed7fd354ce562ef6bc1319db50f79f3b93e88622948", "5a20c7dc77abce34f0f93be93194e3c87c35a186ecce3ad3995a7274313ce57a", "511a5fe62e2d7a8874bbc0ca5ef1f5fdf8acca2e0b2e0831d7d57538bb23deb6", "aab570cb85d0606203b1a7743022902841b629797b2427c6857c7a1c98cfdcc0", "74f939d67e9880a84d526491d5ee0523709bed2d43b5a78071e00b8f4f135d3c", "1ad9e9f3dff84d16f3cc8262fb8041ad1ba08ec593e887f2dc83f07109b9d884",
                          "1caaeb23887c313d48df438d206206b63379b7d49a8138dd8cf37e2818852287",
                          "1e823e21dba365c2b4df45938ac226c2471449f4d48aba62ba05aea62851062e",
                          "30586cd3d5d950f2967f72979fc8493d7877945150dd272a94e2343f160ab7e6",
                          "37070256f0d0ad8d359b4c7dbaf7ba2887aef407eed3e5e66b38f50911147ca7",
                          "4063e6398d22abdcc49fb0805df798b2678034b76f26f6b3c02ab86cba2f8097",
                          "484fb1774eaa6fc1bb70926ff308137c3d09e4e601213438f9273b9a86485476",
                          "6eadb7da6287e5af71076fb8f08a8f6eb194dda9552c336f9f6e553731108d61",
                          "772463328304350fa2ab57b5fd004f568c95e7e67ca9f8a5236e60e26deba92a",
                          "9440a1e24713570ef1cd3451b02dda384b4d704d3b4fc33f21654616c8f60f70",
                          "9e039601ed756cfa8e267730f6fe767f1c39fd413826658f5278581fc6dc3695",
                          "a75ff4ddf13dd8dc7395cb1bc47c817151f09216b806f2d8657ccecb103b71da",
                          "a8c773a6a8bdf51f3169f10ebd543e907e0e9622fc445e79086c081268e23a4a",
                          "bd197d46fd8a4d6a196b875ca3c392780d524aca2dfce7d82ee83791f9152772",
                          "c14895514282fd646e46c43428bad31affcd351bb9c7d42e9e5b02e9dc7dc77b",
                          "c465b64acaca88cb974240405ff349bd230210d91aa3ec63d8484afbec8f4729",
                          "c5b03260482adb4405a52c8443d3f37d70c62d24c24e5fc1dcf9869dca94d329",
                          "ea8610e64ddec8253eddd28ea001360a7448eb4a76767138167dad1f5ce5a2a8",
                          "ff60e568a31999488f1ec5b43e782244292e2150279ec32454464f6483f52b76"},
    "scripts/context.py": {"2bfab7740804aeac2364360e7f4919159c3d6074fd61feaf39321492d9e6a259", "272b7d658f5e42dc5cd7f904f57e4d02fab53dc543e71c0c44168995d4945cc1", "7ef5c4978f4a382c816530c236d23e2398f5e4fa6ac941331441178a1b867389", "8c36f6b551fd07b0ff1292864a9197b3187841bd9f95a97acff1d0ac70261932", "ea5c679d8b8eb70f719a01bfffe6a6690e0f03910cf2a9b31a011c38737d245c", "267b803315ae093bef3be7f8161a2e9bb3002d0be7b85bc4cd9b19776049c845", "52b5834f5d9da74edb4f7fee5b45855bc8bfff44323efaa3d7a202b98e16b92b", "649eb85072166e98383c01f0913eecbdba1d4cb6c7effc9546df2dd4aad714fb", "66952f709d56b61730002c2ad14402bfe905f2efd5a0ce238c4eb842cd53b3f6",
                          "cee7ae092b98b88862a9a2794371b748f4e3de086bcd6f9670d874165b4a9075",
                          "d677daf61267c62c9b74d831d7b09b066b31f4413377d43029196606520b1bbf",
                          "edf5de0d29ecdc99aa1ab29e06eee837fcd90bf782e12facf4b94e0ad7afa61e"},
    "scripts/view.py": {"612652d16936d37c9b667d808c8426b4dfb3e141069bf7e889c62583fe9e5254", "77df75705b52143c720a30e7840e3c895918d850fd435e06f729cdc6ea12ab73", "1b9d2425358f2c88d53aee49720771324661d9f1bc211376e70e1a80515841cb", "00b65b53f4629a31ea570e7accc10b78d649db57e67dd73c9d92e12e54f68b39", "f6946b68060895d91daa913271bb3fda17a812e1a6e01e58e580e66fb5fe2fed", "67ad71444a7afc720872a493605b6a8de0e15d234a4e067eae386cc02dcf4aa5",
                       "931cefb1c72aaabe6c84571dc957d6f32e7bbb63160d1c07bdfa6a02466d25ff",
                       "a87f94ef1752a9a27568fbdc879e25147ba4abc63b22577f9d8be126cff04736",
                       "b2bf3c286274b1298ebad5c31abedd9358edbd5cf70756b6253d86873286cbbd"},
    "scripts/plan.py": {"f21dfdc0314c737dd36e03444a4808645ee1adf83cdaa3946beea050c178055c", "000acce47cdf54fb24292e57b74c01997d2c550b1aefce7b3e04b8ab97f185f4", "0a5f1041d0b596195aa4d4c74f9da4a18449824f895b3b83785f1011d7a2e255", "22a9d7d288dc44963a3609bc88e816c70c78d0afcfe6cd6097c3ab1eba0df50e", "4cf248f60dc4fdfb94f212109c2bf673908f668a5540a1962c866eeffe85839d",
                       "a1808369199e19668e06be90a4f49b355a4a81bf309d1a484661e872e329f2ed",
                       "b8e9813bd08ce49eee78059701cebb0ed8e81c390d7a543505d61a239227a107",
                       "f8632a252bcf32220b0c0122023d329017601e4384ee5523f6fece0236bf99fe"},
    "scripts/javac_cache.py": {"4baa0c270900631e82c35b3f91ed32e3fb1b076904e18cf6cf43fd381b7c7569", "7952325ec3e18a64152c7f2b380d02b9fe4729309f899c3981cdc4b97027ebdc",
                              "8842f0a08f12295458f41fed713e0f2cf392453fc442bfd8377cdbdb304419a0",
                              "c7f67900c9c021f54f821221b54c6da066be16033a9adecb458dddf4ddb4395b",
                              "db6d7bc0cbb188c802689be9389c104b7405d89cce791f7cdfb4b76b37879933",
                              "ef4b0c594b5e0853b285ae2778d16aa73236608361b573a24daa00968a7c768d"},
    "scripts/team.py": {"9569bfbfcaa9935593f503545de87be5b60d01fc2d7c44e748ac096fa2304260", "f0be1fd11a58a84b16418786b4e3fe2d4fa6884fcfadf95f7a63f4915b90967b", "5b1561529a37b99ee5ad4067857ae8f9ef3be923c196142c16d30e06cb4b9ffd", "2b64eefc1db63d7abf5c9447e361dbaa992f3dcca468c2bb8185e0dcafef7c5a", "3f9a193ac84e111036adcc531ddfbf4b10067989282a2a39ddef542018b91318", "9352a4e01b9488703280a426ce5b68f6fc24d406c7e82a3ef7238beec0ae96e3", "003c9963daa196c84fc35b78e9bf58cb122eda35a4d2f5b123d64b5b01c71f94", "3f8a9d3d0be827a7fc354414e57099fc1ce3c1cf00cea35a66d7366859c49926", "944bc5f9d2edfdea795c06540abda19a249f9edb52ddffb7b7e2e066976c345c", "0151965730be7f9c6914675c07b768b38615314b161355a1b8c8c00b5af1e0ea",
                       "36cefee96b4d59d88f82ce90e141fd1deb403afbf84a66bb3e0c46e3dcb44c69",
                       "382f9af23ce8208f1763bc05c6585aa6573bcde22c4a4a6bf3f8a01caaf99f56",
                       "62856a733331e68a8a06c9b21d731a0106897fd4b5cc4d597b4a33903250083f",
                       "7640b6fcafa187f0407a497fc19d0a8841b7a246e274114fee0567d15f4390f6",
                       "9581234661ec8a44ac34f7626497b10fb62e6d9641db6c011f9aa04e2e55c2bb",
                       "97077b620e7e188eb8550570a208b2fbfa809153eb62ca763c5727a9143a5e3c",
                       "abdfa30fcd3aab599f2104db39824fbd07237afbe98c16ff1c750de3d9da0ab5",
                       "d93af73e9ed39dd7aa592bc5aa78f75a8b29e1febcd94ffde5992ca47362316d",
                       "d9bac12c820b8d81d26e86141e4e4395aad0147c17902781549ef2adffe3abff",
                       "e17522b953a5df7fafceba7e4e3995778544b96e4a55d944fd62d3268143c123",
                       "f6332f6903c5094ed5587223d077e294bea3e1189b74951beeea15dd2488667d"},
    "scripts/team_flow.py": {"6b32523c36fede54e973b4ca21baab9e00fa2e47290dbd2a5b206f9cff1ff5d8", "d30ac38efbcfe7108d8962d463d0814e0913040e037dcc8c89192254b3e97bae", "9bce253892dcaef074d799068db03c23ddf8fac904dcb93a9270dea8a13c4996", "25ffc7af0b553b6a21adf7bfcef1528b98864ca50eac9fe115c580a1c1ad5561", "26395d12c129a7e6d07be656e91fd219a862e2595ca008a2698ff78292f3d1f9",
                            "27b64ba739acf314aaef962ccf25db8287b63eece05f2616c59d09b966457e08",
                            "2e9ffd9b45b1952c4db7749d545d3e265004c9d1e08ae68901474ad21fe6d58b",
                            "70b1c35c02bc0fec3042e355ce2bf376dfbb502bb16a96dbf587ba4b11c6a6c0",
                            "b9ee48fc2770a8d05e6c2a2dac72f564e651bc6e2c41bb271fd41557297ef050"},
    "scripts/team_usage.py": {"bff22a74e1a1af5274baf8158ef6329bbd492a83432c10f473720fa2cef461a1", "1edf7c7c9cdb5405c41c97e1d79369b64fa58f639a145fba10b045511c8b9643", "d4902cae2c29516c54a5edb05435c99931faf40620e8f49cea42d1dd600a4486", "ec0cae1d3afdf53670b932c734338e61b0ea6fef18472e5775a6984fc8bda8af", "d5ba72127480c23a78b90e03ff9b4ab6a33b33dd2b0c63bb2cfe1a48f74129ab", "64abae21239ddf5bee2b924d3fae209b92f5da65ff459f49e593330dd33e381f", "339830e59b3356f22eb44211f8daa7e6c8cde7d685d3e063386d9dc6ad556de7",
                             "5297a1f94faa1f0c3f648b9e550296831c09d8261c8ca3120bf3ff1d5c67a928",
                             "7785f1ce7a093fe7e7543afe64f2771ae8b36f5f3b1d567996d7dba90d3d8377",
                             "8c5b5f82351284c777ea616f51fc04d0649c421ba41969a3ff9295432b61b7eb",
                             "c4621f0bb938c6c2020c77d7b6aa90752f9d30b781465c2f5cb7bee6fc96729b",
                             "e335421aec7cc77d3a9aab5ee2ad4c5e4aca6509385ce71dcbe959dde33abfcc",
                             "eb4c2b978f3f923caed1882803df4ddde40412d12bf3b3d0d2204d85af256d35"},
    "scripts/initiative.py": {"2fffd88a50d7c91fbd8f7cf1f54784d494624fa5f708363b2af8e7c7823b3959", "03dc006a5da8e36603d16e48e0c52cb302c9b02c3ca4e43cd84660c5bff767cc",
                             "69ee7e6338d81f912ecb493d9953800a661bd36d3fb2c0ac01f560c011795d05",
                             "9f6b53edfd967be19f3fbc28cd491db0b3b186b46058fa36c074c9152bc02db5",
                             "fa757ea987d4c1a587ff0ac3c622ea8e6a57c97ae94a46d468dce6b161bc9bf9"},
    "scripts/inbox.py": {"34731c3a0d7f39ea6349e80d5d23aa82ff767af7911d24a70c2e2477cea1cda6", "78dc76589f4cd94bae63d6ee20ddb1d93eb5b7a85c1af4cbcf4f45a90e17f05d", "8a0708781e166beb41900cede4a51a2dac561e87b28771f5dcfb3eb626630fff", "6c0ec84aebd80473bd78b908cb67edaff4e415e903c23e238e0490c5883e55c0",
                        "9c8e099d19f0f506e0dcea431e455a6f5c9431ff8b01a26d21533d2303883919",
                        "b58d12ff487e3c5ae66fbbe41975abb13bdcc2aeced196b79599507d52d74198",
                        "c465e861005fa5d69e7ecd8442dcc2c0fae5d62ca6ba6154a9e2e5d009ce1d34",
                        "c7e03e8d1513902e4f9d3c84b3cf46cc00e127a805f9ba6258ee42602ff9a557",
                        "f75621e96441baf9687ea6b241c8575a6565e4a689a66e3eda43ce91d22b279e"},
    "scripts/metrics.py": {"c991e55d4888797df8aaa6eec05d56bc9d9ebc772ed7257131df541a3c602392", "1b8deb18920c236e70289605fad147c681bf2996ba540ad73d9022137ec37535",
                          "1b8deb18920c236e70289605fad147c681bf2996ba540ad73d9022137ec37535"},
    "scripts/review_queue.py": {"8a8ec18fa0a8a5d436d025f4441830b4d279f20233058f147d68c0167542b05b", "a7434656a3c47a55129b895a886a85394122f1a5dbd2051a4b90812d8729804c", "0b9e7ce69f434642e33f378243de3a0bf6de4bdaa595438645c76cd3109792f5", "028c3ca16d70b66d4017c03685b1f15a841fb50602713a8a5bacbca08bddce71",
                               "74c9ed1dfe4bb6857671307263c87f29177cb76c0fdb6a6705f71b63d1ae654a",
                               "ec2cdc42531d0bfa6a591c8f824ea2c06c4d328001791de376981a5d551d25e4"},
    "scripts/team_models.py": {"383416bdb6563c156e728f81ff3ab1ed260b005f4dc4b310edc274283bc48fad", "b2d41e7cd8bd6d295677365c1e0205c28c008ab39de16bd5597a8b67a5e6aa47", "4c4ba745d4878c784b873942c00a60df00550cb2175339bec660cea03c75191e",
                              "95adae0c397ac4a480648a70c47ed6a47fe9d9d249c6559cc3ac14e339b5f739"},
    "scripts/record_wait.js": {"f11ec84161dfba9a09c290147b7be3fb9a19ca17bfbbae56b3b2ba8ac4a71522",
                              "f11ec84161dfba9a09c290147b7be3fb9a19ca17bfbbae56b3b2ba8ac4a71522"},
    "scripts/hk.py": {"1c56614ea0f85f4a3927179af04eab1639a15176f342517b260cda3ca4b7d5b8", "8c080b597baa3c52adf4e07a70e9792404c3e4a2306ba530774a2fd37521216f", "d7f3d2e264667e645c610da2ce9ec3242da4ca8286d60df839167d6e682a528f",
                     "d7f3d2e264667e645c610da2ce9ec3242da4ca8286d60df839167d6e682a528f"},
    "scripts/hk_call.py": {"7e3b91f862ac58fda02459dd8685868b48adec2a6b8ae26b65118da2cf5a7b00", "19fcdfad3370417264eb1f3ae867533fd742f6802f13f75b7a4ba55e21b9af20", "f86670bd9c8c96711cbd70491dc7e8722c61e944bd01c656c74edead51f18f1d", "85ccf5a97a8ceb152c92eeaf88cbc23865a8fdd90389318a993cc02711e1bbee", "fd3d4ec2e47cc30c186155259f3c1a2b8f06e3b71c34f234d175cccbe5daba25",
                          "fd3d4ec2e47cc30c186155259f3c1a2b8f06e3b71c34f234d175cccbe5daba25"},
    "scripts/self_update.py": {"482f4594f1d6848449ffdf8cd628c5bb13dc26216b2212598528bb8d35ce1996", "28409ad5a1689b43c503345aea4171601528838b29116d85d280d04d4eb1a8e5", "281da2b6d3383734409db27f62498f69aec5ff751976d1f3878112c71c07f6c3", "158644a1c785cf013101b8f7903e48d9e4ab33001215ca0bce20bd220e35ac35", "16e2ad4ec15ad8721500cc81001618fb3d5ffa6da6446e59aba99f8f033baffe", "195a7776457b048b195ba94c67ae1104a9550773ae8e0f6d89b5f2af1f85db68",
                              "195a7776457b048b195ba94c67ae1104a9550773ae8e0f6d89b5f2af1f85db68"},
    "scripts/mcp_server.py": {"237d63dcaab7d0ee39b0b5b94ff2bdf3c74072532009b20c034de960e2e90514",
                              "7f991e8b59b179c959ca966885615c8b85ff590bc2070cb0d5af03e113922965", "176651e1c9c9fa66c91e1a57bd93bc6015108ff97d9ea7640fcf4892346b886a", "d9b0db1aa72acc3fbbcc16db3f59af6633337aaea12b1915e65c09a1a4bf7adb", "a2bdfd3f8ec6b3b27a1810ed81eda7d3e83d083fdde30e2d8c2f1a269a369d40", "2efbacdd1fe6d371b048efa95ac0bf0e3ff2a9e4d60b2bd3afc66645960ac508", "f956d288963f32e60206232991b4504db2a5403ff39c0951456ba3cbd112b406", "512d8db1da46d39f1f97a9452284d18a8506a0cfb9ab825752e7eb8fed7afb91", "094c0b8f91d1dc8e07a5e4fd6416f58ae321ae458d7df7377fd4c640542efa44", "25240781faa08176f310790e72f967151f56194e23944c2c3b454235616e180e",
                             "25240781faa08176f310790e72f967151f56194e23944c2c3b454235616e180e"},
    "references/team.md": {"15057d94259d74f400bcc94be6aaa08fe95589d8ebccf19d139158482f7c00ec", "523d55bd800ccde46b2b4e2b6738bf66c8cb8b07243b81fd4d15683eb5c567ae", "4ec376f2f99bf26aa718b7c6b383324253d766d1014c1c010f4225f7558326d2", "438422933f56f7b139768ae1a872bea4e23cad4f180ef381fbeb9c19928593d8", "4ec32c8b879c92534fe6136a65284669ae425500db0857ce6180b1d8b1766ccd", "a8bc46fbc6783e965e0357cf7213052bfd8057c3b612bd488722d9a6ec714251", "867bb23192013df2fb92faaa53289e7ef53e94f952ccf8361497106afdf87dbc",
                           "dca3b38117f99e9b3d795c6be12962f798916bf836cb1e12f447c5c9fedba205", "ee07daed3618a6d8829c89cb7a86ff394b5cef393327157be372789a6a47fe31", "1d91831aebf4041c8ce9e9b5f76f820a90e2d4c5326b916b767c40f8cfc72425",
                          "24506d7be14146b3d65fcb10627669c8592ea41b907a3d00ba0410ac20df0852",
                          "3c7ab8e116edb3e4268233d2423e57088c52d432d2f926d2678e04c4e459d814",
                          "4ec05920e3f6f88400e510b860f710f2bdbbef148c4ed8f04b3b771dbf94a74d",
                          "65503159a05c779e16d8e7e06c562dc16747cb5f51318e64124cbd03621608b5",
                          "736fbc7e632b976b0875ce5b9c7fda02df71a28b091c05d95f6a78e9d2fc8f4a",
                          "93ea23edbd12e99efcdfb3a9ecc5025e6dad32134d89a0a6f16692cf7e947996",
                          "c5197966daa7b804fdd103a587327bd6a134d4075102f56949ca7ff11503cfff",
                          "e506c5be719ae15440430e2948f76f47a8e037157002c9583aa6b12413266ffd"},
    "references/organization.md": {"6f55807eb6e6cdc1225061a510f3ba2dfde740e55cb65b3e3ae308fa0138400e", "354515aa92db1f65b9e5d5ce92339b385ac3b2a1b8a9bdda9046a8e0657300d8", "80f15cfe82dea9826bce2318fd3586043232b1ce7f7dedd2abaf0a671720a9d3", "01fdeb7378e5801e0b7a668f77b94d5af521b66fec12c2895745286dc849565a",
                                  "0d26046f3550a27c590c94c67f46a6fcf5d72581d9fa3f6321fde6d6ef44d667",
                                  "8976d969c1ab578e85b9e8bd30056f401f0b116431373ccb17bd59a76db98b04",
                                  "9366852e64fdd907e295b7c41aa9714516788100bcab76e4e135636de324a96c",
                                  "a5aa2926fe55e01b0f5c49a09f2484718d5e38020b9a2d376b169d3468bea9d8",
                                  "baf68c66f9be2538ae5de628e23f8d3a43427fc312aea5e990c375d2e8db7a6e",
                                  "c70e2c91191845f6ca22c9903b853faa1b2b9baf66dbccf60c53550aeb33ca7d",
                                  "c77b49e21f3e46294ba221184f0cae338fcfff9e625c8cafe042e802c7ae1df9",
                                  "dbcdeeece7cdaef28b8d6ccf614c9d14a52f45c34e81cd8a799d33257556b1ae",
                                  "f25f723dc5b4efe2c6c7c73946be43637a990a8c852db6daf6e63809f40d807a",
                                  "f7d37d0e7966995561a17755e1a78e2cd15b935bf7482b6954bb2d47815519ff"},
    "references/initiative.md": {"9b69f42052ca36e6ed9a41b925597b643476793c9b0b400742ce5057819c89ed", "37d0ad76e77add49eaa2da6cb0ddbfa68c8697a6e7cbbf76bde8535bfcefc997",
                                "7f7984b82baea656b52bb0cc814f1c6173142cec59eeef971002df5bb55a5fa6",
                                "84bd9de3c58c18f2f8f344db662c2295bad93d6164392a64569f9b9a55a6a89d"},
    "references/adaptive-team.md": {"2e0a082773bf678f7fdab117b75ddd84ee71dff21bfe46feab3b73fdcc33933e", "b28d9fc0ef1ea9efff064fe9ebfe34782665a47b49e56d1a8b37f6e674137c71", "dce0392b51ea7cb6fc74442ce2ec2261fcd5d0ffa5b7db4cf962c710475b1534", "c9f7354bda0121320186b416b521a7a742c31e6376d4b84dd8de6ede5b5edb0b",
                                   "c9f7354bda0121320186b416b521a7a742c31e6376d4b84dd8de6ede5b5edb0b"},
    "references/review.md": {"cbd3636ea09607662a01e77e0861441ba7feb462d31ba2b11f176580a77b8f25", "f26c58af3fd9f02506760488067d6883d88f61dfc999a77ba91786c08cb2e9ca", "3a43b6ce31fa395ee6ada149cf97aa4b5b823dad16361acbf624ab3c960ac12d",
                            "980f0903efbe4d5c8c369248e00c48c1b5b861644b802344bae9482fac322496"},
    "references/delivery.md": {"cfb4da7e4faf2c6484614222e0109f00af8f0b58311a1921f51700ec31e8073e", "b766f3cbd9bf8455421e8a7f9fbf5294caee866553bcc4e70ed12672b670493b",
                              "b766f3cbd9bf8455421e8a7f9fbf5294caee866553bcc4e70ed12672b670493b"},
    "references/executor.md": {"da6d672be6e7bee3d393ce2c4edb0fc2e584a4831f42994056751cc66ca8695c", "ff16a2ad56c4c60b63f3f24c492d85059948bb46db0812190875f8cf83e208ce",
                              "ff16a2ad56c4c60b63f3f24c492d85059948bb46db0812190875f8cf83e208ce"},
    "references/telemetry.md": {"bb5c0a5441e9a4e5ce7348810ebce0b3e170497b754047e8436bf2e1ffc305a7", "4f76d3411a4af0f47b80b58f83ddb7ee11fe4333273e8e0157dcd41ec31eeb9a", "860e2fb03dfae33637f4df1cf5762639941bc10d5befb4bd98a382e79ab2ea8c", "68c5b620afa70199eb969d69af56e0d56e6e9ccc94a712adb97117e3252662ab", "bb7de917441341aad8e97701d92c5c31baadc746113027b887fc390fb44350f6",
                               "bb7de917441341aad8e97701d92c5c31baadc746113027b887fc390fb44350f6"},
    "references/commands.md": {"e9069017948107d4e3b436d6a4f5247a41032437d6db094cc97e8676110eacfe", "d549fafbac4cd6e2bc121c5bef6b07d000e98abc7b23d21006b5bf538dfa5ca7", "b3e7d7861b3dd85faae234e7da8cb43d792083ba016667668bf79a8cccc78859", "96a0a6c59a2cc9354194018758ce17a84130bf84016fdf59aa64dae1d3ff9d28",
                              "96a0a6c59a2cc9354194018758ce17a84130bf84016fdf59aa64dae1d3ff9d28"},
    "SKILL.md": {"f45847423d73adca4190c08d8c3f9e3666c64c57e15a0f280201f6c41fac43af", "3e0b37491cdc784038f7ee04b7befa6e954457f2811b0f306ef9e2c80bad1f5e", "e72e312b777e578c2a0407b19c1405e2038a9cf19f88231fac70ba6422bf0c67", "88eff5e7ac5a2e2406a10215809f713da19dfd39d3e303544fa2355609d2a1d0", "75eef856f10dfbc76f8f2761bc1c5ed9761d5bf50f961e4f77ba4e965fe5d36a", "0698479116594666eac57176028d094f367febf1fef749b453a7463c0122db75", "c0785ea305d3253b173ec53f0c6a38d493159ce9891ee552578ddbca0ba5901f", "7f49965fc81e46ae58beb5acc5143f4c7b3076f256dd26b38c74bebf491accfb", "94e967835a4878d9e77ce1ef8a9a42abfa0a9e1d23b1839491030136a220e6ac", "ca994e94f9c9f495aead6d0a34517c9c4d1412f754b5ad80eaf947bc189532f5", "0361046bc779b8d481e61628e771e032ee52cb7741fc0c579fa4714a335da23f",
                "05f1e047658394d492262c29eee05ef0ea89bb414e114db94dc1d0ef5b0db165",
                "1b95907188a5f89a9e6fb68fc0542ee84aae7d12271e887651cae87c61f8ff3c",
                "1e3db4aca4b5c08f98b7d2b87f7eceb1855c4e909e5d3ff7d86100946ab9ca3d",
                "25517966f2c9bab122eb5f4f1640d3b319315abd83dd404ea45dbe85ba607e21",
                "28ef09681d37408629c42702fbd1d83027083be628410e7e6567eb93d09b616d",
                "2b252e5c020de8d7c291730a425242f69e4effdea0765fa8a6d758ee696b877f",
                "2fd3c5a98b62e5bcb5e3edaa2fd516ae8f95ce25efcd90f2c3503c04dffa9a62",
                "304a754f697141e32c606b0ae40c9296b1b6826607d6c73fb953879c0dcd52b4",
                "3f967a044a00911fb9eca322ec556ba80837fc29e3d39ceed040ad9bc2c18e93",
                "4da4e24921f963fe5d2435998a2d49ecef0b70c5eafedf0559cb4c8b25646627",
                "7056a8d41eeab0cb1c7a8f23996acd8528ebbed2cf19be8f9ec365a49aeba0c6",
                "72dc7759caca7a964f0d355b164ab5b4f4b85d497f38247501eb8cc963b96e55",
                "7fba243b9d4aa70e78e97bce8769b491e7873699a245f6c97b8916a52c394443",
                "891ac8c25112c06e0172a922dbaa3d32ba4b04efae667072cf896f6a15e12f53",
                "990fb97f5dd69df97764a2c15f6a62e21494f2e7d790326db5389dc5e10fcdf2",
                "b64ee792bb617a54c4b6f293d337e9112a4b716e8ff8138de9684a18a3ed96f6",
                "cf304b032d491487718069099ab2b4e6c85fae8d757763d36e7cc23f024daef6",
                "dea6cf94cfbc2ec2a4229c994118581672347b90e76f654429a1a458ef9a1498",
                "eb7e220426ad87d78f3d6774f8a8902086f9c631d9d68e8a3d03ff7a12b8e3ba",
                "ee7c25a8cf85b13fda6c7fc9041c5f2cfd93ca101703bcbc78489a62eede214f"},
    "VERSION": {"59854984853104df5c353e2f681a15fc7924742f9a2e468c29af248dce45ce03", "a3fbebb0fe4c012038eceff3148b1602c7da8942fcfe1d563c1de6d377558ae5", "6693435eadb673dda297a0652d241e293e361200bc40db949cf2501d18dd01ee", "99600f6a7bfe6c33ebd1a2518f44a861a67afc40c25da42bc622595716529584", "732f64d874f90788c0199b0c19bfd9b3b6436e140114e53e462832532d74c61c", "2ad7b0c3f0ede58c3165b7a8a5479e890a758d39bfe8683eaef50ce6afad7232", "40f334a825d2bbcb5d7bde863843250f1331e0cab30d22d6bda33e22aec2ca96", "edc8e395a5182e3b231816e191b303407d511f70c1d9cb6d532927cc559c507c", "8248f7772687aff0b103b36898908deb82126337f933e49a5ac04829093c58ff", "f22abd6773ab232869321ad4b1e47ac0c908febf4f3a2bd10c8066140f741261", "59296d23d623ce0adf7675d22156e83b1248c8508135a65dd8a9e38c497e94a8", "1575e1af4a95f12f70b4ee6a6adce8160953d93ea17dc2611b90883ccc3ad3b8",
               "1e5b51cde515396a9fa762909cf8ca6584ccc564b325d2eebeea76175fe95c4d"},
    "scripts/cli_adapters.py": {"20c2b1b856c816d42635921d6ca6ba9a776843fb51336778c7bedc1b0bd5c95b", "8cf0f3dafa4ed53929a04ba5b052f7e8fb6dd9282a21b03952dea9614af4c9ee", "5062b45ba4dcdd81bb6da562762e702d69e6c25edf510df3566ca85ed341357b", "02404fc72bd496035256d346fb4865ca7c391313c6f871b07a77496e08669a59"},
    "scripts/team_driver.py": {"1b670f9f1642a0040c16179fa05afcf1f57893bfad8bd4033c53ed02c3b9be16", "9238ec044b1e2578f61227604b8efb6cbb74fbbeeb0e0c4bb70856d09d28b95a", "e921617399f4da3692c4bcbc9cff8dc80b5765301220711c53ca1e0be204a1e7"},
    "scripts/history.py": {"f1ecc783b88b36b564c9498fbd2cc1915a7f103e0137470439819677b94069ce", "ed555d3cfb96ef175be73b4e7ba73e258e7e65ea0ed095b3df0d10057e564f9f"},
    "references/history.md": {"63956d96bdee718dd65298bc0c9be57cf66fa2a31a58bc80df72289fe28168cf", "dd9eabd88f5cc5287100874cc3c05c4611d1daee60bf0af25c2089f828f77bee", "fa3d755e15ddba0559937904101137acfea3098ef99df9b22a43e57fb17512f0", "9bcef729abb19f244b305b9ff39b74069f7bcec02d8680578cfc6de7fff28525"},
    "scripts/ssh_windows.py": {"cf03c1901534cfa53bf1ba0ccc5fab29f79028d1ed53bc2396a79fb157492aa0"},
    "scripts/build_check.py": {"e32c6d5496721ea0d6a725828363e27b4b5bc5196a1ea4bd5e1922c6ac17a37f", "cc3d4272507b0bf5df21fc7eb4c4179eebd0046c120ab0f3a991841b7fcb6c72"},
    "scripts/symbols.py": {"619cc9e27eb34cb7bfe3d0d7c948f40aa94168a0303f979b94a3627f61ecb2db", "a28bfa62644e1783322f6d8682fc2f505c2e0c90aacab6316b7cff40eabc94bd"},
}
LABELS = {
    "SessionStart": "LoomHarness · session start",
    "PreToolUse": "LoomHarness · action check",
    "Stop": "LoomHarness · Git status",
    "UserPromptSubmit": "LoomHarness · project choice",
}
# Read-only labels used to recognize previously installed, unmodified groups.
PREVIOUS_LABELS = {
    "SessionStart": "LoomHarness · начало работы",
    "PreToolUse": "LoomHarness · проверка действия",
    "Stop": "LoomHarness · состояние Git",
    "UserPromptSubmit": "LoomHarness · выбор для проекта",
}


def codex_config():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"


# Supported clients, hook configuration and event names. A matcher of None
# leaves the event unrestricted by tool name.
CLIS = {
    "codex": (codex_config, (
        ("SessionStart", None),
        ("UserPromptSubmit", None),
        ("PreToolUse", "^(Bash|apply_patch)$"),
        ("Stop", None),
    )),
    "claude": (lambda: Path.home() / ".claude" / "settings.json", (
        ("SessionStart", None),
        ("UserPromptSubmit", None),
        ("PreToolUse", "Bash|Edit|Write|MultiEdit|NotebookEdit"),
        ("Stop", None),
    )),
    "gemini": (lambda: Path.home() / ".gemini" / "settings.json", (
        ("SessionStart", None),
        ("UserPromptSubmit", None),
        ("PreToolUse", "write_file|replace|run_shell_command"),
        ("Stop", None),
    )),
    # Antigravity has startup, tool checks and stop events, without a
    # user-prompt hook.
    "antigravity": (lambda: Path.home() / ".gemini" / "config" / "hooks.json", (
        ("SessionStart", None),
        ("PreToolUse", "write_file|replace|run_shell_command"),
        ("Stop", None),
    )),
}
# Map canonical events to the names used in Gemini's settings file.
CLI_EVENT_NAMES = {"gemini": {"UserPromptSubmit": "BeforeAgent", "PreToolUse": "BeforeTool", "Stop": "AfterAgent"}}


def install_file(path, data, previous):
    if data == previous:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
    temp = Path(tmp.name)
    try:
        if previous is None:
            # Publish a complete file without overwriting a concurrent install.
            if os.name == "nt":
                temp.rename(path)
            else:
                os.link(temp, path)
        else:
            if path.read_bytes() != previous:
                raise ValueError(f"File changed during installation. Retry: {path}")
            temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def hook_command(destination, cli):
    python = Path(sys.executable).resolve().as_posix()
    helper = (destination / "scripts/harness.py").as_posix()
    suffix = "" if cli == "codex" else f" --cli {cli}"
    if os.name != "nt":
        return shlex.join([python, "-I", "-X", "utf8", helper, "hook"]) + suffix
    command = f'"{python}" -I -X utf8 "{helper}" hook{suffix}'
    if any(c in python for c in "$`%&!^") or any(c in helper for c in "$`%&!^"):
        # PowerShell's single-quoted literals keep spaces and punctuation in paths.
        # Base64 passes that fixed command unchanged through the hook runner's shell.
        import base64
        invoke = "& '{}' -I -X utf8 '{}' hook{}; exit $LASTEXITCODE".format(
            python.replace("'", "''"), helper.replace("'", "''"), suffix)
        encoded = base64.b64encode(invoke.encode("utf-16-le")).decode("ascii")
        command = f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}"
    return command


def register_hooks(cli, destination):
    """Register LoomHarness handlers in one client's hook configuration.

    Return (config_path, changed). Supported clients use this group shape:
    {"hooks": {event: [{"matcher": ..., "hooks": [{...}]}]}}.
    """
    config_path, events = CLIS[cli]
    config = config_path()
    original = config.read_bytes() if config.exists() else None
    settings = json.loads(original.decode("utf-8-sig")) if original is not None else {}
    command = hook_command(destination, cli)
    names = CLI_EVENT_NAMES.get(cli, {})

    hooks = settings.setdefault("hooks", {})
    changed = False
    for event, matcher in events:
        label = LABELS[event]
        group = {"hooks": [{"type": "command", "command": command, "timeout": 8, "statusMessage": label}]}
        if matcher:
            group["matcher"] = matcher
        cli_event = names.get(event, event)
        groups = hooks.setdefault(cli_event, [])

        def legacy(entry):
            # Earlier installations used the harness-kit path or a localized
            # product prefix. Replace their groups during upgrades so each
            # event runs one handler.
            return any("harness-kit" in h.get("command", "")
                       or h.get("statusMessage", "").startswith("Харнесс")
                       for h in entry.get("hooks", []))

        old_hook = dict(group["hooks"][0], statusMessage=PREVIOUS_LABELS[event])
        old_group = dict(group, hooks=[old_hook])
        kept = [entry for entry in groups if entry != old_group and not legacy(entry)]
        if len(kept) != len(groups):
            groups[:] = kept
            changed = True
        if group in groups:
            continue
        if any(h.get("statusMessage") == label or h.get("command") == command
               for existing in groups for h in existing.get("hooks", [])):
            raise ValueError(f"A different LoomHarness configuration already exists for {cli_event}. Check {config}.")
        groups.append(group)
        changed = True
    if changed:
        install_file(config, (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode(), original)
    return config, changed


def select_clis(argument):
    if argument != "all":
        return [argument]
    found = [name for name, (config, _) in CLIS.items() if config().parent.is_dir()]
    if not found:
        sys.exit("No supported CLI configuration found. Choose --cli codex|claude|gemini|antigravity.")
    return found


def main():
    if sys.argv[1:] == ["--help"] or sys.argv[1:] == ["-h"]:
        print(__doc__)
        return
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or later is required.")
    cli_argument = "codex"
    if "--cli" in sys.argv:
        position = sys.argv.index("--cli")
        if position + 1 >= len(sys.argv):
            sys.exit("Choose codex, claude, gemini, antigravity or all after --cli.")
        cli_argument = sys.argv[position + 1]
    if cli_argument not in ("codex", "claude", "gemini", "antigravity", "all"):
        sys.exit(f"Unknown CLI: {cli_argument}. Choose codex, claude, gemini, antigravity or all.")
    source = Path(__file__).resolve().parent / "loomharness"
    destination = Path.home() / ".agents/skills/loomharness"

    files = []
    for name in FILES:
        target = destination / name
        data = (source / name).read_bytes()
        existing = target.read_bytes() if target.exists() else None
        # Git may change line endings when checking out on Windows.
        if existing is not None:
            normalized = existing.replace(b"\r\n", b"\n")
            if normalized == data.replace(b"\r\n", b"\n"):
                continue
            if hashlib.sha256(normalized).hexdigest() not in PREVIOUS_RELEASES.get(name, ()):
                raise ValueError(f"The installed file has local changes. Save it before replacing: {target}")
        files.append((target, data, existing))
    for target, data, existing in files:
        install_file(target, data, existing)

    for cli in select_clis(cli_argument):
        config, changed = register_hooks(cli, destination)
        print(f"Installed skill in {destination}. {cli} hooks: {config}"
              + (" (updated)" if changed else " (unchanged)"))
    if cli_argument in ("codex", "all"):
        print("On first setup, open /hooks in Codex CLI and approve the four LoomHarness handlers.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        sys.exit(str(exc))
