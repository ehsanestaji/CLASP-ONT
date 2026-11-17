% ==============================================================================
% CLASP-ONT: Exact Max-Weight 5-Element Poset Lattice Repair Solver (Potassco Clingo)
% ==============================================================================
% Input predicates:
%   mapping(M).                       % Contested correspondence identifier
%   orig_pred(M, P).                  % Input strength: exact | broad | narrow
%   allowed_action(M, A).             % Allowed lattice move A in L(M)
%   utility(M, A, U).                 % Integer utility floor(1000 * w_i * rho(A) * P(A|M))
%   weaken_dir(M, Dir).               % Canonical weaken_ancestor orientation: sub | sup
%   core(C).                          % Minimal conflict core ID
%   core_lit(C, M, Prop).             % Literal (M, Prop) participating in conflict core C
% ==============================================================================

tiebreak_rank(keep_equiv, 5).
tiebreak_rank(downgrade_sub, 4).
tiebreak_rank(downgrade_sup, 3).
tiebreak_rank(weaken_ancestor, 2).
tiebreak_rank(negate_drop, 1).

% Default to exact if orig_pred not explicitly asserted (e.g., unit tests)
has_orig_pred(M) :- orig_pred(M, _).
eff_pred(M, P) :- orig_pred(M, P).
eff_pred(M, exact) :- mapping(M), not has_orig_pred(M).

% 1. Exactly one lattice action per contested mapping
{ selected(M, A) : allowed_action(M, A) } = 1 :- mapping(M).

% 2. Deductive monotonicity over the 5-element refinement poset L(M)
entails_action(M, A, A) :- allowed_action(M, A).
entails_action(M, keep_equiv, downgrade_sub) :-
    eff_pred(M, exact), allowed_action(M, keep_equiv), allowed_action(M, downgrade_sub).
entails_action(M, keep_equiv, downgrade_sup) :-
    eff_pred(M, exact), allowed_action(M, keep_equiv), allowed_action(M, downgrade_sup).
entails_action(M, keep_equiv, weaken_ancestor) :-
    eff_pred(M, exact), allowed_action(M, keep_equiv), allowed_action(M, weaken_ancestor).
entails_action(M, keep_equiv, weaken_ancestor) :-
    eff_pred(M, broad), allowed_action(M, keep_equiv), allowed_action(M, weaken_ancestor), weaken_dir(M, sub).
entails_action(M, keep_equiv, weaken_ancestor) :-
    eff_pred(M, narrow), allowed_action(M, keep_equiv), allowed_action(M, weaken_ancestor), weaken_dir(M, sup).
entails_action(M, downgrade_sub, weaken_ancestor) :-
    allowed_action(M, downgrade_sub), allowed_action(M, weaken_ancestor), weaken_dir(M, sub).
entails_action(M, downgrade_sup, weaken_ancestor) :-
    allowed_action(M, downgrade_sup), allowed_action(M, weaken_ancestor), weaken_dir(M, sup).

% 3. Active logical propositions induced by the selected lattice move
active_prop(M, exact)    :- selected(M, keep_equiv), eff_pred(M, exact).
active_prop(M, dir_sub)  :- selected(M, keep_equiv), eff_pred(M, exact).
active_prop(M, dir_sub)  :- selected(M, keep_equiv), eff_pred(M, broad).
active_prop(M, dir_sub)  :- selected(M, downgrade_sub).
active_prop(M, dir_sup)  :- selected(M, keep_equiv), eff_pred(M, exact).
active_prop(M, dir_sup)  :- selected(M, keep_equiv), eff_pred(M, narrow).
active_prop(M, dir_sup)  :- selected(M, downgrade_sup).
active_prop(M, weak_sub) :- active_prop(M, dir_sub).
active_prop(M, weak_sub) :- selected(M, weaken_ancestor), weaken_dir(M, sub).
active_prop(M, weak_sup) :- active_prop(M, dir_sup).
active_prop(M, weak_sup) :- selected(M, weaken_ancestor), weaken_dir(M, sup).
active_prop(M, act(A))   :- selected(M, Asel), entails_action(M, Asel, A).

% 4. Hard certification integrity constraints: forbid every minimal conflict core
:- core(C), active_prop(M, Prop) : core_lit(C, M, Prop).

% 5. Global max-weight objective (exact integer utility per Section 2.3)
#maximize { U@1, M : selected(M, A), utility(M, A, U) }.

#show selected/2.
