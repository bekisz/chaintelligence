#!/bin/bash

export CHAINTLIGENCE_HOME="/home/szabi/git/chaintelligence/"
export LP_BACKTESTER_HOME="${CHAINTLIGENCE_HOME}/lp-backtester/"
export LP_BACKTESTER_WEB_HOME="/var/www/lp-backtester/html/"
cd ${CHAINTLIGENCE_HOME}
git pull
rm -rf ${LP_BACKTESTER_WEB_HOME}/*
cp -r ${LP_BACKTESTER_HOME}/*  ${LP_BACKTESTER_WEB_HOME}