#!/bin/bash
# check_propose_update_compatibility.sh
#
# Run from your project root (in the artifacts/stock-scanner-api/ folder
# if that's where these files live):
#
#   bash check_propose_update_compatibility.sh
#
# WHY THIS MATTERS
# propose_update() does numpy-based incremental weight updates + drift
# checking. model_training.py trains via sklearn-style pipeline.fit(X, y)
# and saves the result with pickle.dump(). Before wiring these together,
# we need to know if propose_update() expects the SAME kind of model
# object that model_training.py produces -- otherwise wiring them
# together could error out or silently do something meaningless, same
# class of problem as the earlier model_swap_patches schema collision.

echo "===================================================="
echo "1) Full propose_update() function body"
echo "===================================================="
grep -n -A 40 "def propose_update" online_learning.py
echo ""

echo "===================================================="
echo "2) What model object does model_training.py actually produce?"
echo "===================================================="
grep -n -B 5 -A 5 "pipeline.fit\|trained_model" model_training.py
echo ""

echo "===================================================="
echo "3) What does retrain_pipeline.py do with it after training?"
echo "===================================================="
grep -n -B 5 -A 10 "pickle.dump" retrain_pipeline.py
echo ""

echo "===================================================="
echo "4) Does propose_update() reference numpy arrays, sklearn objects,"
echo "   or something else as its expected model/weights input?"
echo "===================================================="
grep -n "np\.\|numpy\|sklearn\|pipeline\|\.fit(\|\.predict(" online_learning.py | head -30
echo ""

echo "===================================================="
echo "5) Any existing docstring/comment explaining what propose_update()"
echo "   was originally designed to accept?"
echo "===================================================="
grep -n -B 2 -A 15 '"""' online_learning.py | grep -A 15 -B 2 -i "propose\|update"
