// Native fetch is available in Node.js 18+

const ENDPOINT = 'https://public.zapper.xyz/graphql';
const { AUTH_HEADER } = require('./secrets.js');
const TARGET_ADDRESS = '0xe34eb31bfd2afea4320b1ce0d1b8ae943afac425';

async function fetchAppBalances() {
    console.log(`\n🔍 Fetching Zapper Balances for ${TARGET_ADDRESS}...\n`);

    const query = `
    query PortfolioApps($addresses: [Address!]!) {
        portfolioV2(addresses: $addresses) {
            appBalances {
                byAddress: byAccount(first: 50) {
                    edges {
                        node {
                            appGroupBalances {
                                edges {
                                    node {
                                        app { slug displayName }
                                        network { name }
                                        balanceUSD
                                        positionBalances {
                                            edges {
                                                node {
                                                    __typename
                                                    ... on AppTokenPositionBalance {
                                                        displayProps {
                                                            label
                                                            images
                                                        }
                                                        balanceUSD
                                                        tokens {
                                                            symbol
                                                            balance
                                                            balanceUSD
                                                            price
                                                        }
                                                    }
                                                    ... on ContractPositionBalance {
                                                        balanceUSD
                                                        displayProps {
                                                            label
                                                            images
                                                        }
                                                        tokens {
                                                            metaType
                                                            token {
                                                                symbol
                                                                balance
                                                                balanceUSD
                                                                price
                                                                decimals
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    `;

    const variables = {
        addresses: [TARGET_ADDRESS]
    };

    try {
        const response = await fetch(ENDPOINT, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': AUTH_HEADER,
                'Accept': 'application/json',
                'Accept-Encoding': 'deflate, gzip'
            },
            body: JSON.stringify({ query, variables })
        });

        const json = await response.json();

        if (json.errors) {
            console.error('❌ GraphQL Errors:', JSON.stringify(json.errors, null, 2));
            return;
        }

        const accounts = json.data?.portfolioV2?.appBalances?.byAddress?.edges || [];

        if (accounts.length === 0) {
            console.log('ℹ️  No application data found.');
            return;
        }

        console.log(`✅ Fetched Data. Processing...\n`);

        accounts.forEach(acc => {
            const appGroups = acc.node.appGroupBalances.edges;

            appGroups.forEach(group => {
                const app = group.node.app;
                const net = group.node.network;
                const bal = group.node.balanceUSD;

                if (bal < 0.01) return; // Skip dust

                console.log(`📱 ${app.displayName} (${net.name})`);
                console.log(`   Total: $${bal.toFixed(2)}`);

                const positions = group.node.positionBalances?.edges || [];
                positions.forEach(pos => {
                    const node = pos.node;
                    const label = node.label || node.displayProps?.label || 'Position';
                    const posBal = node.balanceUSD || 0;
                    const type = node.__typename;

                    console.log(`   - [${type}] ${label}: $${posBal.toFixed(2)}`);

                    // Handle ContractPositionBalance (Nested token structure)
                    if (type === 'ContractPositionBalance' && node.tokens) {
                        const supplyTokens = [];
                        const rewardTokens = [];

                        node.tokens.forEach(wrapper => {
                            const t = wrapper.token;
                            if (!t) return;

                            const item = {
                                symbol: t.symbol,
                                balance: t.balance,
                                balanceUSD: t.balanceUSD,
                                metaType: wrapper.metaType
                            };

                            const metaType = (wrapper.metaType || 'supplied').toLowerCase();
                            if (metaType === 'claimable' || metaType === 'reward') {
                                rewardTokens.push(item);
                            } else {
                                supplyTokens.push(item);
                            }
                        });

                        if (supplyTokens.length > 0) {
                            console.log(`     Assets:`);
                            supplyTokens.forEach(t => {
                                console.log(`       • ${parseFloat(t.balance).toFixed(4)} ${t.symbol} ($${t.balanceUSD.toFixed(2)})`);
                            });
                        }

                        if (rewardTokens.length > 0) {
                            console.log(`     Unclaimed:`);
                            rewardTokens.forEach(t => {
                                console.log(`       • ${parseFloat(t.balance).toFixed(6)} ${t.symbol} ($${t.balanceUSD.toFixed(2)})`);
                            });
                        }

                    }
                    // Handle AppTokenPositionBalance (Flat structure)
                    else if (node.tokens) {
                        node.tokens.forEach(t => {
                            const tokenBal = t.balance || 0;
                            const tokenBalUSD = t.balanceUSD || 0;
                            console.log(`     • ${parseFloat(tokenBal).toFixed(4)} ${t.symbol} ($${tokenBalUSD.toFixed(2)})`);
                        });
                    }
                });
                console.log('---------------------------------------------------');
            });
        });

    } catch (error) {
        console.error('❌ Request failed:', error);
    }
}

fetchAppBalances();
